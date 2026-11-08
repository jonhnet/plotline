# Repository and publication

Plotline Studios is published as [jonhnet/plotline](https://github.com/jonhnet/plotline).
Its development history starts with a curated source snapshot on November 8,
2026. All new work belongs on this history. The earlier private history includes
archaeological archives and inputs that are not distributed.

## Git workflow

Branch or create a worktree from current `main`. Never merge branches from the
old private repository; apply a reviewed patch or cherry-pick a specific fix.
Install the push check to catch accidental history reconnection:

```sh
git config core.hooksPath .githooks
git config plotline.publicRoot "$(git rev-list --max-parents=0 main)"
```

The check rejects branches or tags containing another root commit. Keep secrets,
original captures, live state and private correspondence outside tracked files.

On Jon's development host, `/root/plotline` keeps its service and data paths.
`origin` is `/mnt/dev-source/repos/plotline.git`; `github` is the public remote.
Push work to the private home repository and publication updates to GitHub:

```sh
git push origin main
git push github main
```

The earlier source and Git metadata are preserved privately at
`/root/plotline-private-20261107`; its home remote is
`/mnt/dev-source/repos/plotline-private-20261107.git`. Old worktrees remain at
their original paths, connected to that archive with commit/push guards.
Create new worktrees from the current repository. Both the archive and current
workspace are registered in the host's backup selection.

## Source package

`tools/package_public_source.py` exported reviewed committed files without
private history. `SOURCE_MANIFEST.json` records the initial source files and
hashes; later commits change those files normally. It is an initial-snapshot
record, not a current-tree checksum. The private archive retains the export
report and cutover records. Do not repeatedly replace public history.

The source includes application code, manifests and lockfiles, tests, setup,
AGPL license, dependency inventory and README assets. Examples carry linked
authoring inputs. Unlinked derived PDFs, intents, original captures, provider
caches and live documents are excluded.

Jon's original example assets are [CC BY 4.0](readme/ASSET-LICENSE.md).
Brunofloresmx granted permission on November 7, 2026 to redistribute the
Spider-Man illustration for this demonstration. See [credits](readme/CREDITS.md);
the correspondence stays private.

## Validation and distribution

The source setup was exercised on the existing Ubuntu host with fresh Python
dependencies, both Rust release binaries, the optional terrain extension and
isolated Studio browser checks. See [setup](GETTING_STARTED.md) for commands.
The historical diagnostic corpus is excluded; some archived scripts refer to
those private inputs.

This is an experimental source release. There is no prebuilt Podman image.
Container packaging needs persistent state, process supervision, dependency
notices and documented LAN networking for IPP discovery. Windows is untested
and community-supported, not a release gate.

Contributions retain AGPL and the physical-placement, clipping, pen-width and
isolated-browser tests. Plotting remains an explicit operator action.
