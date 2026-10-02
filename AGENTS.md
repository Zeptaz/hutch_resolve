# Agent working instructions

Read [context.md](context.md), the relevant owner plan under [docs/plans](docs/plans), and [shared contracts](docs/contracts.md) before work. The user's current task determines what you may implement; a task checkbox is not authorization to expand scope.

After meaningful progress, update context.md and the relevant owner plan with task IDs, changed behavior, verification command/result, implementation commit when available, blockers and remaining work. Mark a task complete only when implemented and verified. Distinguish static/unit/mock/browser/live verification. Do not describe a passing helper suite as full integration qualification.

Keep shared contracts authoritative. Update their specifications and affected owner plans before introducing incompatible behavior. Harry owns Resolve business services, migrations, authorization and external Voice integration; Tevin owns conversation behavior through the facade; Jayith owns both frontends. Preserve unrelated work and never commit secrets or real customer data.
