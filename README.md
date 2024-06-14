# OPTIMADE Property Tools

This reporty contains tools to work with OPTIMADE properties.
The primary tool is `process_schemas` (in the `bin` subdirectory), which converts between different property definition formats.

The recommended way to set up the Python dependencies is via a virtual environment:
```
  python3 -m venv venv
  venv/bin/pip3 install -r requirements.txt
  source venv/bin/activate
```

Other ways to set up dependencies with the relevant package names:

- apt: `apt install python3-yaml python3-jsonschema python3-markdown python3-mdx-math python3-pygments python3-importlib-metadata`
- pip: `pip install PyYAML jsonschema markdown python-markdown-math pygments`
- conda: `conda install python=3 pyyaml jsonschema markdown python-markdown-math pygments`

## OPTIMADE Property Definitions

The section `Property Definitions` of the OPTIMADE specification defines an output format-agnostic way to declare properties that can be communicated via OPTIMADE to describe physical quantities and related data.
The format uses a subset of JSON Schema extended with OPTIMADE-specific identifiers, as allowed by the JSON Schema standard with identifiers prefixed with `x-optimade-`.
Hence, they can be used as schemas to validate data items using standard tools for JSON Schema.

As described in more detail below, the OPTIMADE consortium publishes the current and past standardized sets of Property Definitions, with an index available at the following URL:

  - https://schemas.optimade.org/defs/

Anyone can, of course, publish their own sets of Property Definitions under any URL they like.
See [Creating database-specific definitions](#creating_database_specific_definitions) and [Editing and contributing Property Definitions](#editing_and_contributing_property_definitions) below for more information.

See also the `schemas/README.md` file in the [OPTIMADE specification repository](https://github.com/Materials-Consortia/OPTIMADE).


## Creating database-specific definitions

The repository provides a directory `example` to demonstrate how this is done.
This example represents a typical situation where:

- The `structures` and `files` entry types are inherited from the standard source files and extended with implementation-specific information in `x-optimade-implementation`.
- A couple of extra database-specific properties (`_exmpl_cell_volume`, `_exmpl_magnetic_moment`) are defined and added to the extended `structures` entry type.
- Files to be used for `json-ld` and `json-schema` validation are generated with the necessary modifications.

A recommended workflow is:

* Copy `example` into a working directory of your own, e.g., `my-database`.
* Edit the settings related to the specifics of the database, e.g., the path to the OPTIMADE standard definitions and the domain name used for the static URIs, in the Makefile `GNUMakefile` in this directory.
* Edit the content under `src` in this directory.
* Execute `make` to processes the files into `output` (similarly to how the OPTIMADE standard definition files are processed.)

If you want to host your definitions online, serve the contents of `output` at the appropriate base URL, e.g., `https://example.com/schemas/`.

Note that the parameters `schemas_html_pretty=true` and `schemas_html_ext=true` documented under [Property Definitions in the OPTIMADE repository](property_definitions_in_the_optimade_repository) also works here.
