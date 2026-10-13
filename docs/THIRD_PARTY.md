# Third party foundations

Device Test Lab combines an original simulated-device test workflow and Studio interface with established open-source packages. Exact JavaScript versions and integrity metadata are in frontend/pnpm-lock.yaml; installed Python versions are pinned in requirements.lock. Each dependency retains its own license.

| Foundation | Use |
|---|---|
| [React](https://react.dev/) and [Vite](https://vite.dev/) | Browser application and production build |
| [shadcn/ui](https://ui.shadcn.com/) | Button composition pattern and component-design reference |
| [Radix Slot](https://www.radix-ui.com/primitives/docs/utilities/slot) | Button composition with semantic elements |
| [Class Variance Authority](https://cva.style/) | Button variants |
| clsx and tailwind-merge | Class-name composition |
| [Lucide](https://lucide.dev/) | Interface icons |
| [FastAPI](https://fastapi.tiangolo.com/) and Uvicorn | Local API and server |
| [scikit-learn](https://scikit-learn.org/) and NumPy | Logistic regression, transformations and numerical arrays |
| [Playwright](https://playwright.dev/) | Browser integration verification |
| pytest, Ruff and Mypy | Python verification |

The Button implementation adapts the standard shadcn/ui composition using Radix Slot, CVA and a class-name helper. The rest of the Studio layout is custom; a complete third-party dashboard was not copied. The shadcn/ui source is available under its [MIT license](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md), copyright 2023 shadcn. The full notice is preserved in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

The simulated scenarios and recorded example results are original to this project. The interface uses system fonts and loads no remote fonts, illustrations or runtime AI service.
