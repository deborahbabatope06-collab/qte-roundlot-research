# config/

| file | what | committed? |
|---|---|---|
| `instruments_1102.csv` | the exchange's Instruments CSV for the 2 November 2026 term (official round lots, `lot_size`) | check the exchange is happy for it to be public first; delete it from git if not (tests skip without it) |
| `desk.toml` | the team's strategy config that ran (copy from the team repo) | **no**, git-ignored |
| `desk-YYYY-MM-DD.toml` | a different desk.toml for one day, passed with `--desk-toml-for` | **no**, git-ignored |
