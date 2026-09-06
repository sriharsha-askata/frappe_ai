# Example consumer fixture

`example_consumer_custom_fields.json` is intentionally **not** listed in
`frappe_ai/hooks.py` and is not loaded by `frappe_ai`. Copy it into a consumer
app's fixtures and adapt the field name and placement for that app.

A consumer can load only its own fields with the usual Frappe filter:

```python
fixtures = [
    {
        "dt": "Custom Field",
        "filters": [
            ["name", "in", ["AI Knowledge Source-custom_consumer_field"]]
        ],
    }
]
```

This keeps consumer-specific fields on `AI Knowledge Source` without changing
frappe_ai's DocType schema.
