---
name: dataforseo
display_name: "DataForSEO"
description: "SEO data suite — keyword research, on-page analysis, backlinks, domain age, and Google Business Profile"
category: seo
icon: bar-chart
skill_type: sandbox
catalog_type: addon
requirements: "httpx>=0.25"
resource_requirements:
  - env_var: DATAFORSEO_LOGIN
    name: "DataForSEO Login"
    description: "DataForSEO account email"
  - env_var: DATAFORSEO_PASSWORD
    name: "DataForSEO Password"
    description: "DataForSEO account password"
config_schema:
  properties:
    default_location:
      type: string
      label: "Default Location"
      description: "Location name for keyword and business data (e.g. United States, Canada, San Diego,California,United States)"
      placeholder: "United States"
      default: "United States"
      group: defaults
    default_language:
      type: string
      label: "Default Language Code"
      description: "Language code for API requests"
      placeholder: "en"
      default: "en"
      group: defaults
    keyword_limit:
      type: number
      label: "Keyword Suggestion Limit"
      description: "Max keyword suggestions to return per request"
      default: 20
      group: defaults
    search_volume_batch_size:
      type: number
      label: "Search Volume Batch Size"
      description: "Max keywords per search volume request (API max: 700)"
      default: 100
      group: defaults
    enable_javascript_rendering:
      type: boolean
      label: "Enable JS Rendering"
      description: "Enable JavaScript rendering for on-page analysis (slower but more accurate for SPAs)"
      default: true
      group: defaults
    include_subdomains:
      type: boolean
      label: "Include Subdomains in Backlinks"
      description: "Include subdomains when analyzing backlink profiles"
      default: true
      group: defaults
    target_domains:
      type: text
      label: "Target Domains"
      description: "Default domains to analyze (one per line)"
      placeholder: "example.com\ncompetitor.com"
      group: defaults
    keyword_rules:
      type: text
      label: "Keyword Research Rules"
      description: "Rules for keyword research behavior"
      placeholder: "- Focus on long-tail keywords with low difficulty\n- Always include search volume and difficulty\n- Group keywords by intent (informational, transactional, navigational)"
      group: rules
    analysis_rules:
      type: text
      label: "Analysis Rules"
      description: "Rules for on-page and backlink analysis"
      placeholder: "- Always check mobile rendering\n- Flag pages with load time > 3s\n- Report missing meta descriptions and H1 tags"
      group: rules
    report_template:
      type: text
      label: "Report Template"
      description: "Template for SEO analysis reports"
      placeholder: "## SEO Audit: {domain}\n\n### Keywords\n{keywords}\n\n### On-Page\n{onpage}\n\n### Backlinks\n{backlinks}\n\n### Recommendations\n{recommendations}"
      group: templates
    safety_rules:
      type: text
      label: "Safety Rules"
      description: "Constraints and limits for API usage"
      placeholder: "- Max 100 keywords per search volume batch\n- Always confirm before running expensive on-page analysis\n- Cache results when possible"
      group: rules
tool_schema:
  name: dataforseo
  description: "SEO data suite — keyword research, on-page analysis, backlinks, domain age, and Google Business Profile"
  parameters:
    type: object
    properties:
      action:
        type: "string"
        description: "Which operation to perform"
        enum: ['keyword_suggestions', 'search_volume', 'keyword_difficulty', 'onpage_analysis', 'backlinks_summary', 'domain_age', 'gbp_info']
      keyword:
        type: "string"
        description: "Seed keyword for keyword_suggestions"
        default: ""
      keywords:
        type: "string"
        description: "Comma-separated keywords for search_volume or keyword_difficulty"
        default: ""
      url:
        type: "string"
        description: "URL for onpage_analysis"
        default: ""
      domain:
        type: "string"
        description: "Domain for backlinks_summary or domain_age"
        default: ""
      place_id:
        type: "string"
        description: "Google Place ID for gbp_info"
        default: ""
      location_name:
        type: "string"
        description: "Override location name"
        default: ""
      limit:
        type: "integer"
        description: "Max results"
        default: 20
    required: [action]
---
# DataForSEO

Comprehensive SEO data suite powered by the DataForSEO API.

## Keyword Research
- **keyword_suggestions** — Get keyword ideas from a seed keyword. Provide `keyword` and optional `location_name`, `limit`.
- **search_volume** — Get monthly search volume for keywords. Provide `keywords` (comma-separated, max 700).
- **keyword_difficulty** — Get difficulty scores (0-100) for keywords. Provide `keywords` (comma-separated).

## On-Page Analysis
- **onpage_analysis** — Full on-page SEO audit of a URL. Provide `url`. Returns meta tags, headings, content analysis, load metrics.

## Backlinks & Domain
- **backlinks_summary** — Get backlink count and domain authority. Provide `domain`.
- **domain_age** — Get domain registration date via WHOIS. Provide `domain`.

## Google Business Profile
- **gbp_info** — Get GBP profile info, attributes, reviews summary. Provide `place_id` and `location_name`.
