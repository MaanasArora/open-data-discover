from time import sleep, time
from io import BytesIO
from typing import Annotated

from bson.objectid import ObjectId
import numpy as np
import pandas as pd
from fastapi import FastAPI, Depends, UploadFile, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pymongo import UpdateMany, IndexModel
from pymongo.database import Database
import networkx as nx
import httpx
from tqdm import tqdm

from settings import settings
from database import get_db
from model import get_linkages, create_graph, generate_component_entities


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _process():
    time_end = time_start = time()
    print(f"Processing started at {time_start}")

    db = get_db()

    print("Cleaning up")
    raw_datasets = list(db.raw_datasets.find())
    raw_datasets = [
        raw_ds for raw_ds in raw_datasets if db[raw_ds["name"]].count_documents({})
    ]
    if db.datasets.count_documents({}):
        db.domains.drop()
        db.entities.drop()
        for dataset in db.datasets.find():
            db.drop_collection(dataset["name"])
        db.datasets.drop()

    time_last, time_end = time_start, time()
    print(f"Step completed at {time_end}")
    print(f"Time taken: {time_end - time_last:.2f} seconds")

    columns = []
    col_names_to_old_names = {}

    print("Processing datasets")
    for ds in tqdm(raw_datasets):
        collection = db[ds["name"]]

        col_names = ds.get("columns", [])
        for i, col in enumerate(col_names):
            if "." in col:
                names = col.split(".")[::-1]

                new_col = {}
                for name in names:
                    new_col = {name: new_col}
                new_col = str(new_col)

                col_names[i] = new_col
                col_names_to_old_names[new_col] = col

        columns.extend(
            [
                {
                    "dataset": ds["name"],
                    "name": col,
                    "data": [
                        d["_id"]
                        for d in collection.aggregate([{"$group": {"_id": f"${col}"}}])
                    ],
                    "name_field": col,
                }
                for col in col_names
                if col != "_id"
            ]
        )

    time_last, time_end = time_end, time()
    print(f"Step completed at {time_end}")
    print(f"Time taken: {time_end - time_start:.2f} seconds")

    print("Creating domains")
    linkages = get_linkages(columns)
    G = create_graph(linkages)

    connected_components = list(nx.connected_components(G))
    for component in tqdm(connected_components):
        component = list(component)
        if len(component) < 2:
            continue

        component_columns = [columns[i] for i in component]
        average_linkage = linkages[np.ix_(component, component)].mean()

        entities = generate_component_entities(component_columns)

        component_columns = [
            {"dataset": col["dataset"].replace("_raw", ""), "name": col["name"]}
            for col in component_columns
        ]

        domain = db.domains.insert_one(
            {"columns": component_columns, "linkage": average_linkage}
        )

        entity_ids = db.entities.insert_many(
            [{"domain": domain.inserted_id, "label": entity} for entity in entities]
        )

    del linkages, G, connected_components, columns

    time_last, time_end = time_end, time()
    print(f"Step completed at {time_end}")
    print(f"Time taken: {time_end - time_last:.2f} seconds")

    db.domains.create_index([("columns.dataset", 1)])
    db.entities.create_index([("domain", 1)])

    print("Creating datasets")
    domains = list(db.domains.find())

    domains_entity_mappings = {}
    for domain in tqdm(domains):
        columns = domain["columns"]
        linkage = domain["linkage"]

        entities = list(db.entities.find({"domain": domain["_id"]}))
        entity_mapping = {entity["label"]: entity["_id"] for entity in entities}

        domains_entity_mappings[domain["_id"]] = entity_mapping

    for raw_dataset in tqdm(raw_datasets):
        raw_collection = db[raw_dataset["name"]]
        df = pd.DataFrame(list(raw_collection.find()))

        domains_dataset = [
            domain
            for domain in domains
            if any(
                col["dataset"] == raw_dataset["name"].strip("_raw")
                for col in domain["columns"]
            )
        ]

        for domain in tqdm(domains_dataset, leave=False):
            columns = domain["columns"]
            entity_mapping = domains_entity_mappings[domain["_id"]]
            for col in tqdm(columns, leave=False):
                if col["dataset"] == raw_dataset["name"].strip("_raw"):
                    col_name = col["name"]
                    if col_name not in df.columns:
                        continue
                    df[col_name] = df[col_name].map(entity_mapping, na_action="ignore")

        dataset_name = raw_dataset["name"].replace("_raw", "")

        if db.datasets.find_one({"name": dataset_name}):
            db.drop_collection(raw_dataset["name"])

        collection = db.create_collection(dataset_name)
        collection.insert_many(df.to_dict(orient="records"))

        db.datasets.insert_one(
            {
                "name": dataset_name,
                "size": db[dataset_name].count_documents({}),
                "columns": raw_dataset["columns"],
            }
        )

    time_last, time_end = time_end, time()
    print(f"Step completed at {time_end}")
    print(f"Time taken: {time_end - time_last:.2f} seconds")

    print()
    print(f"Processing completed at {time_end}")
    print(f"Total time taken: {time_end - time_start:.2f} seconds")

    minutes, seconds = divmod(time_end - time_start, 60)
    print(f"** Processing complete in ** {minutes:.0f} minutes {seconds:.2f} seconds")


def fetch_open_dataset(id: str, db):
    base_url = "https://ckan0.cf.opendata.inter.prod-toronto.ca"
    url = f"{base_url}/api/3/action/package_show?id={id}"

    response = httpx.get(url)
    if response.status_code != 200:
        raise HTTPException(status_code=404, detail="Dataset not found")

    dataset = response.json()["result"]
    resources = dataset["resources"]

    count_inserted = 0
    for resource in resources:
        if resource["format"] == "CSV":
            url = resource["url"]

            response = httpx.get(url, timeout=180)
            if response.status_code != 200:
                raise HTTPException(status_code=404, detail="Dataset not found")

            df = pd.read_csv(BytesIO(response.content), low_memory=False)
            df = df.set_index(df.columns[0])

            ds_name = f"{dataset['name']}_raw"
            if db.raw_datasets.find_one({"name": ds_name}):
                continue

            collection = db.create_collection(ds_name)
            collection.insert_many(df.to_dict(orient="records"))

            ds_info = {
                "name": ds_name,
                "columns": df.columns.tolist(),
                "size": df.shape[0],
            }

            db.raw_datasets.insert_one(ds_info)
            count_inserted += 1

    return count_inserted


@app.get("/")
def read_root():
    return {"Hello": "World"}


@app.get("/datasets/raw")
def read_raw_datasets(db: Annotated[Database, Depends(get_db)]):
    datasets = db.raw_datasets.find()
    datasets = [
        {
            "id": str(ds["_id"]),
            "name": ds["name"],
            "size": ds["size"],
        }
        for ds in datasets
    ]
    return datasets


@app.get("/datasets")
def read_datasets(db: Annotated[Database, Depends(get_db)]):
    datasets = db.datasets.find()
    datasets = [
        {
            "id": str(ds["_id"]),
            "name": ds["name"],
            "columns": ds["columns"],
            "size": ds["size"],
        }
        for ds in datasets
    ]
    return datasets


@app.post("/datasets/raw")
def upload_dataset(
    datasets: list[UploadFile], db: Annotated[Database, Depends(get_db)]
):
    ds_ids = []
    for dataset in datasets:
        ds_name = f"{dataset.filename.split('.')[-2]}_raw"

        if db.raw_datasets.find_one({"name": ds_name}):
            continue

        df = pd.read_csv(dataset.file)
        df = df.set_index(df.columns[0])

        collection = db.create_collection(ds_name)
        collection.insert_many(df.to_dict(orient="records"))

        ds_info = {
            "name": ds_name,
            "columns": df.columns.tolist(),
            "size": df.shape[0],
        }
        ds = db.raw_datasets.insert_one(ds_info)
        ds_ids.append(str(ds.inserted_id))

    return {"message": "Datasets uploaded", "ids": ds_ids, "count": len(ds_ids)}


@app.post("/datasets/open/{id}")
def post_fetch_open_dataset(id: str, db: Annotated[Database, Depends(get_db)]):
    count_inserted = fetch_open_dataset(id, db)

    return {"message": "Dataset(s) uploaded", "count": count_inserted}


@app.post("/datasets/open")
def post_fetch_open_datasets(ids: list[str], db: Annotated[Database, Depends(get_db)]):
    count_inserted = 0
    for id in ids:
        count_inserted += fetch_open_dataset(id, db)

    return {"message": "Dataset(s) uploaded", "count": count_inserted}


@app.post("/datasets/crawl")
def post_crawl_open_datasets(
    db: Annotated[Database, Depends(get_db)],
    background_tasks: BackgroundTasks,
    skip: int = 0,
    limit: int = 100,
):
    existing_datasets = db.raw_datasets.find()
    existing_datasets = [ds["name"] for ds in existing_datasets]

    url = "https://ckan0.cf.opendata.inter.prod-toronto.ca/api/3/action/package_search"

    ids = []
    total_traversed = 0
    while limit > len(ids):
        response = httpx.get(url, params={"start": total_traversed})

        datasets = response.json()["result"]["results"]
        total_traversed += len(datasets)
        total_count = response.json()["result"]["count"]

        if len(ids) + len(datasets) > total_count:
            break

        if total_traversed > skip:
            continue

        for ds in datasets:
            if any([resource["format"] == "CSV" for resource in ds["resources"]]):
                ds_name = f"{ds['name']}_raw"
                if ds_name not in existing_datasets:
                    ids.append(ds["id"])

    count_inserted = 0
    for id in tqdm(ids):
        try:
            count_inserted += fetch_open_dataset(id, db)
        except Exception as e:
            print(e)
        sleep(2)

    return {"message": "Dataset(s) uploaded", "count": count_inserted}


@app.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, db: Annotated[Database, Depends(get_db)]):
    dataset = db.datasets.find_one(ObjectId(dataset_id))
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    db.drop_collection(dataset["name"])
    db.datasets.delete_one({"_id": ObjectId(dataset_id)})

    return {"message": "Dataset deleted"}


@app.delete("/datasets/raw/{dataset_id}")
def delete_raw_dataset(dataset_id: str, db: Annotated[Database, Depends(get_db)]):
    dataset = db.raw_datasets.find_one(ObjectId(dataset_id))
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    db.raw_datasets.delete_one({"_id": ObjectId(dataset_id)})

    return {"message": "Dataset deleted"}


@app.get("/datasets/{dataset_name}")
def read_dataset(
    dataset_name: str, db: Annotated[Database, Depends(get_db)], page: int = 0
):
    dataset = db.datasets.find_one({"name": dataset_name})
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    del dataset["_id"]

    count = 40
    data = list(db[dataset["name"]].find(limit=count, skip=page * count))
    for d in data:
        del d["_id"]
        for k, v in d.items():
            if pd.isna(v):
                d[k] = None
            if isinstance(v, ObjectId):
                label = db.entities.find_one(v)["label"]
                if pd.isna(label):
                    label = None
                d[k] = {"entity_id": str(v), "label": label}

    return {"dataset": dataset, "content": data}


@app.get("/domains")
def read_domains(db: Annotated[Database, Depends(get_db)]):
    domains = db.domains.find()
    domains = [
        {
            "id": str(d["_id"]),
            "columns": d["columns"],
            "linkage": d["linkage"],
        }
        for d in domains
    ]
    return domains


@app.get("/domains/{domain_id}")
def read_domain(domain_id: str, db: Annotated[Database, Depends(get_db)]):
    domain = db.domains.find_one(ObjectId(domain_id))
    if not domain:
        raise HTTPException(status_code=404, detail="Domain not found")

    entities = list(db.entities.find({"domain": domain["_id"]}, limit=20))
    for entity in entities:
        entity["id"] = str(entity["_id"])
        del entity["_id"]
        del entity["domain"]

    entities = [e for e in entities if not pd.isnull(e["label"])]
    domain["entities"] = entities
    del domain["_id"]

    return domain


@app.get("/entities/{entity_id}")
def read_entity(entity_id: str, db: Annotated[Database, Depends(get_db)]):
    entity = db.entities.find_one(ObjectId(entity_id))
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")

    del entity["_id"]

    return {
        "id": entity_id,
        "label": entity["label"] if not pd.isnull(entity["label"]) else None,
        "domain": str(entity["domain"]),
    }


@app.post("/process")
def process(
    db: Annotated[Database, Depends(get_db)], background_tasks: BackgroundTasks
):
    raw_datasets = list(db.raw_datasets.find())
    if len(raw_datasets) < 2:
        raise HTTPException(
            status_code=400, detail="At least two datasets are required"
        )

    background_tasks.add_task(_process)
    return {"status": "processing"}
