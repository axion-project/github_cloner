# GitHub Complete Repository Cloner & Updater (SSH Edition)

This Python script clones and updates **all** repositories you have access to on GitHub  
(user-owned, organization, collaborations), exclusively using SSH for git operations.

## Features

- Uses GitHub GraphQL API v4 for fast repo listing  
- Supports cloning & updating repos concurrently with async  
- Clones via SSH — no tokens stored in git credentials  
- Stores repos in `~/code-blacksite` by default  
- Safe and fast incremental updates (`git fetch` + `git pull --ff-only`)

## Requirements

- Python 3.7+  
- `aiohttp` library (`pip install aiohttp`)  
- Git CLI installed and available in PATH  
- SSH key configured with GitHub (https://github.com/settings/keys)  
- GitHub Personal Access Token with `repo` scope for API access (no git cloning)

## Usage

1. Clone this repository or download `github_cloner.py`  
2. Export your GitHub token in your environment:

```bash
export GITHUB_TOKEN=your_personal_access_token_here
python3 github_cloner.py

python3 github_cloner.py --target ~/my_repos --concurrent 10
