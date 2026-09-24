name: jcc-telemetry
description: Forward Hermes lifecycle events to the JEV COMMAND CENTER (telemetry only; no actuation)
events:
  - gateway:startup
  - session:start
  - session:end
  - session:reset
  - agent:start
  - agent:step
  - agent:end
  - command:*
