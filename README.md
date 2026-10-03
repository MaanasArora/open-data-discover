# Discovery tool for Toronto Open Data: WIP

This project aims to make it easier for users of the [City of Toronto's open data](https://open.toronto.ca/) to discover relationships
between data elements. The aspiration is to make open data more discoverable for everyone!

Currently, we are building and exposing a data-driven algorithm to find _joinable columns_ between datasets, i.e.
columns that are likely to share the same domain of values in the catalog. This is helpful for people who are interested in finding
columns that could be joined (relational/SQL-style) to their column of choice.

## Contributing

Contributions are welcome from anyone! Please take a look at the GitHub project for the V1 milestone:
https://github.com/users/MaanasArora/projects/2 and comment
on any issue you would like to work on. A brief note on your approach/plan would be appreciated.

### Areas to contribute

We are actively looking for contributions in the following. Feel free to open an issue if there isn't already on the roadmap:

- UI/UX improvements to the current interface (e.g. accessibility issues, misleading cues)
- Design and branding work
- Bug fixes
- Validation for the current algorithm
- User feedback
- Refactors and improvements of the code (keeping current functionality)

These items are out of scope for V1, but we would love to have them later. Feel free to share your ideas for discussion, however
complete PRs for these features may be reviewed later or not merged to the current version:

- Overhaul of the UI/UX
- Creation of databases or complex modifications/additions to the data model
- UI features that significantly change the user goal (e.g., interleaving through multiple datasets)
- Features that change the evaluation system / require re-evaluation (e.g., graph algorithms, language analysis)

## Deployment

See the deployed site at https://maanasarora.github.io/open-data-discover
