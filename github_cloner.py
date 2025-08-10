#!/usr/bin/env python3
"""
GitHub Complete Repository Cloner & Updater (Linux Edition, SSH Mode)

Clones or updates ALL repositories (user-owned, collaborations, org repos) 
you have access to, public & private, using GitHub API v4 (GraphQL).
Cloning/updating done exclusively over SSH (requires SSH keys setup).
"""

import os
import sys
import asyncio
import aiohttp
from pathlib import Path
from typing import List, Dict
import subprocess
import argparse

# ===== CONFIGURATION =====
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN', 'YOUR_TOKEN_HERE')  # Still needed for API
TARGET_DIR = os.path.expanduser('~/code-blacksite')
MAX_CONCURRENT_CLONES = 8


class GitHubCloner:
    def __init__(self, token: str, target_dir: str):
        self.token = token
        self.target_dir = Path(target_dir)
        self.session = None
        self.username = None

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(limit=20)
        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(
            headers={'Authorization': f'Bearer {self.token}'},
            connector=connector,
            timeout=timeout
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def graphql_query(self, query: str, variables: Dict = None) -> Dict:
        payload = {'query': query}
        if variables:
            payload['variables'] = variables
        async with self.session.post('https://api.github.com/graphql', json=payload) as response:
            if response.status != 200:
                raise Exception(f"GraphQL query failed: {response.status}")
            data = await response.json()
            if 'errors' in data:
                raise Exception(f"GraphQL errors: {data['errors']}")
            return data['data']

    async def fetch_all_repositories(self) -> List[Dict]:
        print("🔍 Fetching all accessible repositories...")

        query = """
        query($repoCursor: String, $orgCursor: String) {
          viewer {
            login
            repositories(first: 100, after: $repoCursor, affiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
              nodes {
                name
                nameWithOwner
                sshUrl
                isPrivate
                owner { login }
              }
              pageInfo { hasNextPage endCursor }
            }
            organizations(first: 100) {
              nodes {
                login
                repositories(first: 100, after: $orgCursor) {
                  nodes {
                    name
                    nameWithOwner
                    sshUrl
                    isPrivate
                    owner { login }
                  }
                  pageInfo { hasNextPage endCursor }
                }
              }
            }
          }
        }
        """

        all_repos = []
        repo_cursor = None

        # Step 1: Fetch personal + collaboration repos
        while True:
            data = await self.graphql_query(query, {'repoCursor': repo_cursor})
            viewer = data['viewer']
            if not self.username:
                self.username = viewer['login']

            repos = viewer['repositories']
            all_repos.extend(repos['nodes'])
            if not repos['pageInfo']['hasNextPage']:
                break
            repo_cursor = repos['pageInfo']['endCursor']

        # Step 2: Fetch org repos
        for org in viewer['organizations']['nodes']:
            org_cursor = None
            while True:
                data = await self.graphql_query(query, {'orgCursor': org_cursor})
                orgs = data['viewer']['organizations']['nodes']
                if not orgs:
                    break
                org_repos = orgs[0]['repositories']
                all_repos.extend(org_repos['nodes'])
                if not org_repos['pageInfo']['hasNextPage']:
                    break
                org_cursor = org_repos['pageInfo']['endCursor']

        # Deduplicate by nameWithOwner
        unique_repos = {r['nameWithOwner']: r for r in all_repos}
        return list(unique_repos.values())

    async def clone_or_update_repo(self, repo: Dict) -> Dict:
        name = repo['nameWithOwner']
        repo_path = self.target_dir / repo['name']
        result = {'name': name, 'status': 'unknown', 'path': str(repo_path)}

        url = repo['sshUrl']  # SSH only!

        try:
            if repo_path.exists() and (repo_path / '.git').exists():
                # Fetch and update safely
                proc_fetch = await asyncio.create_subprocess_exec(
                    'git', 'fetch', '--all', '--prune',
                    cwd=repo_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc_fetch.communicate()

                proc_pull = await asyncio.create_subprocess_exec(
                    'git', 'pull', '--ff-only',
                    cwd=repo_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc_pull.communicate()

                if proc_pull.returncode == 0:
                    result['status'] = 'updated'
                else:
                    result['status'] = 'update_failed'
                    result['error'] = stderr.decode()
            else:
                # Clone fresh
                proc_clone = await asyncio.create_subprocess_exec(
                    'git', 'clone', url, str(repo_path), '--progress',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await proc_clone.communicate()

                if proc_clone.returncode == 0:
                    result['status'] = 'cloned'
                else:
                    result['status'] = 'clone_failed'
                    result['error'] = stderr.decode()

        except Exception as e:
            result['status'] = 'error'
            result['error'] = str(e)

        return result

    def print_summary(self, results: List[Dict]):
        cloned = sum(1 for r in results if r['status'] == 'cloned')
        updated = sum(1 for r in results if r['status'] == 'updated')
        failed = sum(1 for r in results if 'failed' in r['status'] or r['status'] == 'error')

        print("\n" + "=" * 50)
        print("🎉 Backup Summary")
        print("=" * 50)
        print(f"🆕 Cloned: {cloned}")
        print(f"🔄 Updated: {updated}")
        if failed:
            print(f"❌ Failed: {failed}")
            for r in results:
                if 'failed' in r['status'] or r['status'] == 'error':
                    print(f"  • {r['name']}: {r.get('error', 'Unknown error')}")
        print(f"📁 Location: {self.target_dir}")

    async def run(self):
        if self.token == 'YOUR_TOKEN_HERE':
            print("❌ Please set your GITHUB_TOKEN environment variable to use the GitHub API.")
            return

        self.target_dir.mkdir(parents=True, exist_ok=True)
        repos = await self.fetch_all_repositories()
        print(f"📦 Total repositories found: {len(repos)}")
        if not repos:
            print("🤔 No repositories found or insufficient permissions.")
            return

        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CLONES)

        async def process_repo(repo):
            async with semaphore:
                result = await self.clone_or_update_repo(repo)
                status_emoji = {
                    'cloned': '🆕',
                    'updated': '🔄',
                    'clone_failed': '❌',
                    'update_failed': '⚠️',
                    'error': '💥'
                }
                print(f"  {status_emoji.get(result['status'], '❓')} {result['name']}")
                return result

        results = await asyncio.gather(*(process_repo(r) for r in repos))
        self.print_summary(results)


async def main():
    parser = argparse.ArgumentParser(description="Clone and update all your GitHub repos via SSH")
    parser.add_argument('--token', help="GitHub personal access token (or set GITHUB_TOKEN env var)")
    parser.add_argument('--target', default=TARGET_DIR, help="Target directory for cloning")
    parser.add_argument('--concurrent', type=int, default=MAX_CONCURRENT_CLONES, help="Max concurrent clone/update operations")
    args = parser.parse_args()

    token = args.token or GITHUB_TOKEN
    global MAX_CONCURRENT_CLONES
    MAX_CONCURRENT_CLONES = args.concurrent

    async with GitHubCloner(token, args.target) as cloner:
        await cloner.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Interrupted by user.")
    except Exception as e:
        print(f"💥 Unexpected error: {e}")
        sys.exit(1)
