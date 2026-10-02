# 009 — Desk screens and the UI shell

How the Frappe AI screens in the Desk are put together, and how to add an admin-style screen if you need one.

## What exists today

| Screen | What it is | Files |
|---|---|---|
| **Frappe AI workspace** | A normal Frappe workspace with grouped shortcuts: Agents & Tools, Knowledge, Sessions & Runs, Configuration, Automation & Memory, MCP & Integrations | `frappe_ai/frappe_ai/workspace/frappe_ai/frappe_ai.json` (installed as a fixture, `hooks.py` `fixtures`) |
| **Chat page** at `/app/frappe-ai` | A Desk **Page** that only creates an empty container and asks the React app to mount itself there | `frappe_ai/frappe_ai/page/frappe_ai/frappe_ai.js` and `.json` |
| **Chat panel** | A slide-in panel available on every Desk page | `frontend/src/hosts/deskPanel.tsx` |
| **Form helpers** | Buttons on DocType forms (for example *Test Connection* on `AI Model` and `AI MCP Connection`) | the `*.js` file next to each DocType |

The chat itself is one React app ([005](005-frontend-contract.md)). It is built from `frontend/src/main.tsx` into `frappe_ai/public/frappe_ai_panel/frappe_ai_panel.js` and `.css`, which `hooks.py` loads on every Desk page (`app_include_js`, `app_include_css`, with a file-modified version in the URL so browsers pick up new builds).

`main.tsx` does two things: on `app_ready` it mounts the slide-in panel, and it publishes `frappe.frappe_ai.mountStandalonePage` so the page script above can mount the full-page version. Both hosts render the same `App`; only the host code differs. If the bundle has not loaded yet, the page shows "assets are still loading, refresh".

## Admin-style screens are not built

Setup is done on the normal DocType forms and the workspace shortcuts. There is no custom tabbed admin dashboard for agents, connections or health. If you want one, the pattern below follows how Frappe Assistant Core's own admin page works.

### Recipe for a Desk page with tabs

1. **Create a Page** (DocType `Page`) under the module, for example `frappe_ai/frappe_ai/page/ai_admin/ai_admin.json` plus `ai_admin.js`. The JSON gives it a name, title and the roles allowed to open it (`roles`); the JS sets up the page:

   ```javascript
   frappe.pages["ai-admin"].on_page_load = function (wrapper) {
       const page = frappe.ui.make_app_page({ parent: wrapper, title: __("AI Admin"), single_column: true });
       // build tabs, then load data with frappe.call({ method: "frappe_ai.api....." })
   };
   ```

2. **Put data behind whitelisted methods** and check permissions there, for example `frappe.has_permission("AI Settings", "read", throw=True)`. The page's `roles` list only hides the menu entry; the server check is the real protection. `api/mcp.get_mcp_health_dashboard` is an existing example of a read model for such a screen.
3. **Link it** from the workspace JSON (a shortcut or a link) so people can find it.
4. **Style** with Frappe's own classes first; add a small stylesheet only where needed.
5. For anything dynamic and large, prefer adding it to the React app instead, as another route behind the same host, so it shares the state and API layer.

Keep each screen to one job (health of connections, agent readiness, usage). Show what is wrong and the button to fix it.

## Conventions for form scripts

- Save a dirty document before calling a server method that reads it (`(frm.is_dirty() ? frm.save() : Promise.resolve()).then(...)`), as the *Test Connection* buttons do.
- Use `frappe.show_alert` for results and `frm.reload_doc()` afterwards so the form shows new status fields.
- Wrap all user-facing text in `__()`.
