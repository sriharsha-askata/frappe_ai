# FAC Admin Style UI Implementation Guide

> How to implement the same desk page routing and navigation style as FAC Admin in frappe_ai

---

## Overview

FAC Admin (`/app/fac-admin`) is a **desk page** - a Frappe-built-in page accessible from the sidebar/search. This guide shows how to implement the same style in frappe_ai.

---

## Key Characteristics of FAC Admin Style

| Aspect | Implementation |
|--------|----------------|
| **URL** | `/app/fac-admin` (desk page) |
| **Access** | Via search bar, sidebar, or direct URL |
| **Navigation** | Tab-based (Dashboard, Tools, Plugins, etc.) |
| **API Calls** | `frappe.call({ method: ... })` |
| **UI Components** | Cards, toggles, tables, dialogs |
| **Styling** | Uses Frappe's design system + custom CSS |

---

## Implementation Steps for frappe_ai

### Step 1: Create the Page Controller

```python
# frappe_ai/www/fac-admin.py

import frappe
from frappe.utils import today
from frappe import _

def get_context(context):
    """Render the FAC Admin page"""
    
    # Check permissions
    if not frappe.has_permission("AI Settings", "read"):
        frappe.throw(_("No permission"), frappe.PermissionError)
    
    context.brand_html = "AI Admin"
    context.top_bar_items = [
        {"label": _("Dashboard"), "url": "/app/fac-admin"},
        {"label": _("Connections"), "url": "/app/ai-connections"},
        {"label": _("Agents"), "url": "/app/ai-agents"},
    ]
```

### Step 2: Register the Page (via app config or hooks)

```python
# frappe_ai/hooks.py

website_route_rules = [
    {"from_route": "/ai-admin/<path:app_path>", "to_route": "ai-admin"},
]

# For desk page access via /app/ai-admin
desk_page_js = "/assets/frappe_ai/js/ai_admin.js"
desk_page_css = "/assets/frappe_ai/css/ai_admin.css"
```

### Step 3: Create the JavaScript

```javascript
// frappe_ai/public/js/ai_admin.js

frappe.pages['ai-admin'].on_page_load = function(wrapper) {
    const $container = $(wrapper).append(`
        <div class="ai-admin-container">
            <div class="page-container">
                <!-- Tab Navigation -->
                <div class="ai-top-tabs">
                    <button class="ai-top-tab active" data-tab="dashboard">
                        <i class="fa fa-dashboard"></i> Dashboard
                    </button>
                    <button class="ai-top-tab" data-tab="connections">
                        <i class="fa fa-plug"></i> Connections
                    </button>
                    <button class="ai-top-tab" data-tab="agents">
                        <i class="fa fa-robot"></i> Agents
                    </button>
                    <button class="ai-top-tab" data-tab="settings">
                        <i class="fa fa-cog"></i> Settings
                    </button>
                </div>

                <!-- Tab Content Panels -->
                <div class="ai-tab-panel active" id="dashboard-panel">
                    <!-- Dashboard content -->
                </div>
                <div class="ai-tab-panel" id="connections-panel">
                    <!-- Connections content -->
                </div>
                <div class="ai-tab-panel" id="agents-panel">
                    <!-- Agents content -->
                </div>
                <div class="ai-tab-panel" id="settings-panel">
                    <!-- Settings content -->
                </div>
            </div>
        </div>
    `);

    // Initialize
    aiAdmin.init($container);
};

var aiAdmin = {
    init: function($container) {
        this.$container = $container;
        this.state = {
            viewMode: 'dashboard',
            autoRefreshEnabled: true,
            toggleInProgress: {}
        };
        
        this.bindEvents();
        this.loadDashboard();
    },

    bindEvents: function() {
        const self = this;
        
        // Tab switching
        this.$container.find('.ai-top-tab').on('click', function() {
            const tab = $(this).data('tab');
            self.switchTab(tab);
        });
    },

    switchTab: function(tabName) {
        // Update tab active state
        this.$container.find('.ai-top-tab').removeClass('active');
        this.$container.find(`.ai-top-tab[data-tab="${tabName}"]`).addClass('active');
        
        // Update panel visibility
        this.$container.find('.ai-tab-panel').removeClass('active');
        this.$container.find(`#${tabName}-panel`).addClass('active');
        
        this.state.viewMode = tabName;
        
        // Load tab content
        if (tabName === 'dashboard') this.loadDashboard();
        else if (tabName === 'connections') this.loadConnections();
        else if (tabName === 'agents') this.loadAgents();
        else if (tabName === 'settings') this.loadSettings();
    },

    loadDashboard: function() {
        const self = this;
        this.$container.find('#dashboard-panel').html('<div class="ai-skeleton-wrap">...</div>');
        
        frappe.call({
            method: 'frappe_ai.api.admin.get_dashboard_stats',
            callback: function(response) {
                if (response.message) {
                    self.renderDashboard(response.message);
                }
            }
        });
    },

    renderDashboard: function(stats) {
        const html = `
            <div class="ai-stats-grid">
                <div class="ai-stat-card ai-stat-card--connections">
                    <h3>Connections</h3>
                    <div class="ai-stat-value">${stats.connections || 0}</div>
                    <div class="ai-stat-label">Active MCP connections</div>
                </div>
                <div class="ai-stat-card ai-stat-card--agents">
                    <h3>Agents</h3>
                    <div class="ai-stat-value">${stats.agents || 0}</div>
                    <div class="ai-stat-label">Configured agents</div>
                </div>
                <div class="ai-stat-card ai-stat-card--executions">
                    <h3>Executions</h3>
                    <div class="ai-stat-value">${stats.executions_today || 0}</div>
                    <div class="ai-stat-label">Runs today</div>
                </div>
            </div>
        `;
        this.$container.find('#dashboard-panel').html(html);
    },

    loadConnections: function() {
        // Load connections list
    },

    loadAgents: function() {
        // Load agents list
    },

    loadSettings: function() {
        // Load settings
    }
};
```

### Step 4: Create the CSS

```css
/* frappe_ai/public/css/ai_admin.css */

/* Container */
.ai-admin-container {
    max-width: 1400px;
    margin: 0 auto;
    padding: 20px;
}

/* Card */
.ai-card {
    background: var(--card-bg);
    border-radius: var(--border-radius-md);
    padding: 20px;
    margin-bottom: 20px;
    box-shadow: var(--shadow-sm);
    border: 1px solid var(--border-color);
}

.ai-card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border-color);
}

.ai-card-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--heading-color);
}

/* Stats Grid */
.ai-stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 20px;
}

.ai-stat-card {
    background: var(--card-bg);
    border-radius: var(--border-radius-md);
    padding: 20px;
    border-left: 4px solid var(--primary);
    box-shadow: var(--shadow-sm);
}

.ai-stat-card--connections { border-left-color: var(--primary); }
.ai-stat-card--agents { border-left-color: var(--green-500); }
.ai-stat-card--executions { border-left-color: var(--blue-500); }

.ai-stat-card h3 {
    margin: 0 0 12px 0;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-muted);
    text-transform: uppercase;
}

.ai-stat-value {
    font-size: 32px;
    font-weight: 600;
    color: var(--heading-color);
    margin-bottom: 8px;
}

.ai-stat-label {
    font-size: 13px;
    color: var(--text-muted);
}

/* Tab Navigation */
.ai-top-tabs {
    display: flex;
    gap: 0;
    border-bottom: 2px solid var(--border-color);
    margin-bottom: 20px;
}

.ai-top-tab {
    padding: 12px 20px;
    border: none;
    background: transparent;
    cursor: pointer;
    font-size: 14px;
    font-weight: 500;
    color: var(--text-muted);
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    transition: all 0.15s;
    display: flex;
    align-items: center;
    gap: 6px;
}

.ai-top-tab:hover {
    color: var(--heading-color);
    background: var(--bg-color);
}

.ai-top-tab.active {
    color: var(--primary);
    border-bottom-color: var(--primary);
    font-weight: 600;
}

/* Tab Panel */
.ai-tab-panel {
    display: none;
}

.ai-tab-panel.active {
    display: block;
}

/* Toggle Switch */
.ai-switch {
    position: relative;
    display: inline-block;
    width: 44px;
    height: 24px;
}

.ai-switch input {
    opacity: 0;
    width: 0;
    height: 0;
}

.ai-slider {
    position: absolute;
    cursor: pointer;
    top: 0;
    left: 0;
    right: 0;
    bottom: 0;
    background-color: var(--gray-400);
    transition: .3s;
    border-radius: 24px;
}

.ai-slider:before {
    position: absolute;
    content: "";
    height: 18px;
    width: 18px;
    left: 3px;
    bottom: 3px;
    background-color: white;
    transition: .3s;
    border-radius: 50%;
}

.ai-switch input:checked + .ai-slider {
    background-color: var(--green-500);
}

.ai-switch input:checked + .ai-slider:before {
    transform: translateX(20px);
}

/* Empty State */
.ai-empty-state {
    padding: 40px 20px;
    text-align: center;
    color: var(--text-muted);
}

.ai-empty-state .fa {
    font-size: 28px;
    opacity: 0.5;
    margin-bottom: 8px;
}

/* Status Badge */
.ai-status-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
}

.ai-status-badge.active {
    background: var(--green-100);
    color: var(--green-700);
}

.ai-status-badge.inactive {
    background: var(--gray-100);
    color: var(--gray-600);
}
```

---

## Directory Structure

```
frappe_ai/
├── frappe_ai/
│   └── www/
│       └── ai-admin.py           # Page controller
├── public/
│   ├── css/
│   │   └── ai_admin.css         # Styles
│   └── js/
│       └── ai_admin.js          # JavaScript
└── hooks.py                       # Register page
```

---

## Key APIs to Implement

| API | Purpose |
|-----|---------|
| `frappe_ai.api.admin.get_dashboard_stats` | Dashboard statistics |
| `frappe_ai.api.admin.get_connections` | List MCP connections |
| `frappe_ai.api.admin.toggle_connection` | Enable/disable connection |
| `frappe_ai.api.admin.get_agents` | List AI agents |
| `frappe_ai.api.admin.toggle_agent` | Enable/disable agent |

---

## Summary

| Step | Action |
|------|--------|
| 1 | Create `www/ai-admin.py` - page controller |
| 2 | Register in `hooks.py` via `desk_page_js/css` |
| 3 | Create `public/js/ai_admin.js` - tab-based UI |
| 4 | Create `public/css/ai_admin.css` - FAC-style styling |
| 5 | Implement APIs in `frappe_ai/api/admin.py` |
| 6 | Access via `/app/ai-admin` |

---

## Related

- FAC Admin JS: `frappe_assistant_core/public/js/fac_admin_*.js`
- FAC Admin CSS: `frappe_assistant_core/public/css/fac_admin.css`
