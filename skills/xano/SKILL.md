---
name: xano
display_name: "Xano"
description: "Query Xano database tables and call custom API endpoints (read-only)"
category: productivity
icon: database
skill_type: sandbox
catalog_type: addon
requirements: "httpx>=0.25"
resource_requirements:
  - env_var: XANO_INSTANCE_DOMAIN
    name: "Xano Instance Domain"
    description: "Your Xano instance domain (e.g. xktx-zdsw-4yq2.n7.xano.io)"
  - env_var: XANO_API_GROUP_BASE_URL
    name: "Xano API Group Base URL"
    description: "Full base URL for the API group (e.g. https://xktx-zdsw-4yq2.n7.xano.io/api:F6QZTCZX)"
  - env_var: XANO_METADATA_API_KEY
    name: "Xano Metadata API Key"
    description: "API key for the Xano Metadata API (for table/schema operations). Found in Instance Settings → Metadata API."
  - env_var: XANO_AUTH_TOKEN
    name: "Xano Auth Token"
    description: "Bearer token for authenticated custom API endpoints (optional, leave blank if endpoints are public)"
config_schema:
  properties:
    default_table:
      type: string
      label: "Default Table"
      description: "Default table name for queries"
      placeholder: "clients"
      group: defaults
    default_endpoint:
      type: string
      label: "Default Endpoint"
      description: "Default API endpoint path for call_endpoint"
      placeholder: "/clientSummary"
      group: defaults
    default_sort_column:
      type: string
      label: "Default Sort Column"
      description: "Column to sort by when not specified"
      placeholder: "created_at"
      group: defaults
    default_sort_direction:
      type: select
      label: "Default Sort Direction"
      description: "Sort direction when not specified"
      options: ["asc", "desc"]
      default: "desc"
      group: defaults
    result_limit:
      type: number
      label: "Default Result Limit"
      description: "Maximum records to return by default"
      default: 50
      group: defaults
    allowed_tables:
      type: text
      label: "Allowed Tables"
      description: "Tables the agent is allowed to query (one per line, empty = all)"
      placeholder: "clients\nprojects\ninvoices"
      group: rules
    query_rules:
      type: text
      label: "Query Rules"
      description: "Rules and constraints for API calls and queries"
      placeholder: "- Always paginate results\n- Default sort by created_at desc\n- Include only active records\n- Never expose sensitive columns (password, ssn)"
      group: rules
    endpoint_rules:
      type: text
      label: "Endpoint Rules"
      description: "Rules for which endpoints to call and when"
      placeholder: "- Use /clientSummary for overview requests\n- Use /clientDetails/{id} for individual lookups\n- Always pass authentication params"
      group: rules
    response_template:
      type: text
      label: "Response Template"
      description: "Template for formatting query results"
      placeholder: "Found {count} records in {table}:\n{results}\n\nShowing page {page} of {total_pages}."
      group: templates
tool_schema:
  name: xano
  description: "Query Xano database tables and call custom API endpoints (read-only)"
  parameters:
    type: object
    properties:
      action:
        type: "string"
        description: "Which operation to perform"
        enum: ['call_endpoint', 'list_tables', 'get_table_schema', 'query_table']
      endpoint_path:
        type: "string"
        description: "API endpoint path for call_endpoint (e.g. /clientSummary)"
        default: ""
      params:
        type: "string"
        description: "JSON string of query parameters for call_endpoint"
        default: ""
      table_name:
        type: "string"
        description: "Table name for query_table or get_table_schema"
        default: ""
      page:
        type: "integer"
        description: "Page number for query_table (1-based)"
        default: 1
      per_page:
        type: "integer"
        description: "Records per page for query_table (max 100)"
        default: 50
      search:
        type: "string"
        description: "Search query for query_table"
        default: ""
      sort_column:
        type: "string"
        description: "Column to sort by for query_table"
        default: ""
      sort_direction:
        type: "string"
        description: "Sort direction for query_table (asc or desc)"
        default: "asc"
    required: [action]
---
# Xano

Query Xano database tables and call custom API endpoints (read-only).

## Custom API Endpoints
- **call_endpoint** — Call any custom GET API endpoint in your Xano API group. Specify `endpoint_path` (e.g. `/clientSummary`) and optional `params` (JSON query params).

## Table Operations (Metadata API)
- **list_tables** — List all tables in the Xano instance
- **get_table_schema** — Get column schema for a specific table
- **query_table** — Query records from a table with pagination, search, and sorting
