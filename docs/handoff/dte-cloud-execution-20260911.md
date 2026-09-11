# DTE cloud execution options — 2026-09-11

## Current goal
Find a temporary cloud execution path for DTE while the local Windows machine has only 16 GB RAM and cannot comfortably dedicate ~8 GB to an isolated agent/runtime.

## Confirmed workload constraints
- DTE itself is light Python computation; ~8 GB RAM should be sufficient when not also running the local desktop/frontend workload.
- GPU is not required for embeddings.
- Embeddings can be obtained through the free Google AI Studio API, so the executor mainly needs unrestricted outbound HTTPS.
- The desired environment therefore needs roughly: Linux/Python shell, >=8 GB RAM, outbound Internet, git/repository access, and preferably persistent enough state for iterative work.
- ChatGPT Work Cloud's browser has web access, but the compute/shell environment should not be assumed to provide arbitrary outbound network access to Python processes. Therefore Work Cloud itself is not currently a direct executor for AI Studio embedding calls.

## Best free candidate found so far
### GitHub Codespaces
- Smallest documented machine: 2 cores, 8 GB RAM, 32 GB storage.
- Codespaces allow outbound connections to the public Internet by default, including cloud APIs.
- GitHub Free personal accounts currently include 120 core-hours/month and 15 GB-month storage. On a 2-core codespace this corresponds to about 60 wall-clock active hours/month.
- Verified GitHub Education students receive the GitHub Pro-equivalent allowance: 180 core-hours/month, i.e. about 90 wall-clock active hours/month on the 2-core machine.
- This is a strong fit for DTE if the repository can run normally in an Ubuntu/devcontainer environment.

## Other free candidate
### Google Colab Free
- Free hosted notebook; code executed in the runtime can make Internet requests, including API calls.
- Standard-memory VMs are provided, but exact RAM is not guaranteed and runtimes can be reclaimed; free runs are limited and interactive-use policies apply.
- Free Colab explicitly restricts remote-control patterns such as SSH/remote desktop and bypassing the notebook UI, so it is less attractive as a general-purpose external agent runtime.
- ChatGPT Work Cloud could in principle operate Colab through its cloud browser, but this would be browser automation rather than a direct shell integration and is likely less robust for iterative repository work.

## Current working conclusion
For a zero-cost temporary DTE executor, GitHub Codespaces is currently the most promising path. The remaining question is how directly ChatGPT Work can orchestrate it; Work Cloud itself does not remove the need for an external network-enabled runtime.
