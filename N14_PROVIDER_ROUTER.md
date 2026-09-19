\# N14 — Production Coding Provider Layer



\## Purpose



N14 introduces a bounded provider-routing layer above the N13 AI Coding Brain.



The layer separates:



\- provider selection

\- provider configuration

\- capability filtering

\- cost policy

\- retry handling

\- provider fallback

\- secret-safe configuration



from the core autonomous coding workflow.



N14 does not bypass the safety boundaries established by N13, N12, or the approval lifecycle.



\---



\## System Architecture



```mermaid

flowchart TD

&#x20;   A\[Improvement Proposal] --> B\[N14 Provider Router]



&#x20;   B --> C{Provider Policy}



&#x20;   C -->|Eligible| D\[Configured Coding Provider]

&#x20;   C -->|Rejected| E\[Skip Provider]



&#x20;   D --> F\[N13 AI Coding Brain]



&#x20;   F --> G{Valid PatchCandidate?}



&#x20;   G -->|Yes| H\[Patch Review]

&#x20;   G -->|No| I\[Bounded Retry / Fallback]



&#x20;   I --> D



&#x20;   H --> J\[Sandbox Validation]

&#x20;   J --> K\[READY\_FOR\_APPROVAL]



&#x20;   K --> L\[Human Approval]

&#x20;   L --> M\[N12 GitHub Worker]

&#x20;   M --> N\[Draft PR]
