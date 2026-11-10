# Project Instructions

- The owner is Melvin.
- Keep the conversation in Spanish.
- Write source code and source-code comments in English.
- Write generated documentation in English.
- Work incrementally and verify each change before continuing.
- Keep this repository limited to the custom UBA orchestration system. Do not add the game source or unrelated Unreal Engine source.
- Keep Epic/Uba source changes isolated in the Perforce integration workspace.
- Do not use credentials unless they are required for an explicitly requested operation. Local credentials, when needed, are stored at `/Users/meluko/develop/starbreeze/ai_agent_skills/.secrets`.
- When code changes are made, propose an English commit message in the final response.
- Prefer the standard library for the MVP unless a dependency is already declared in `requirements.txt`.
- Every new endpoint or protocol change must be documented and covered by a test where practical.

- For orchestrator deployment TLS, read the certificate chain and matching private key from `/mnt/devops/resources/ssl/2026/star_starbreeze_com.pem` and `star_starbreeze_com.key` on the selected Jenkins node. Preserve certificate validation and installation outside the checkout with restricted permissions; do not reintroduce the Jenkins TLS archive credential.
