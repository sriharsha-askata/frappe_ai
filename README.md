### Frappe AI

frappe ai

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch main
bench install-app frappe_ai
```

### Optional Dependencies

PDF extraction uses `pdfplumber` and RapidOCR by default. For faster, higher-fidelity PDF
conversion, install Docling in the bench environment:

```bash
bench pip install "docling>=1.0.0"
```

When Docling is present, PDF extraction uses it first and falls back to the default extractor if
Docling is unavailable or fails.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/frappe_ai
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit
