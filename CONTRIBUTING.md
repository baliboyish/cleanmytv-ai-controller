# Contributing

Thanks for your interest. cmtv is a small, single-file Python CLI
(standard library only, except image conversion, which uses
Pillow — see `requirements.txt`) plus a unittest suite that runs
against a mock provider — no TV needed for development.

## Development

```sh
CMTV_MOCK=1 python3 -m unittest discover -s tests
```

- Every command must work against the mock. If a provider behaviour
  matters (a side effect, an error mode, a staging rule), reproduce
  it in `MockAdb` and pin it with a test.
- Config changes must go through the single `apply_change` path
  (validate → snapshot → undo entry → `set64` → verify). Do not add
  side doors around it.
- Asset operations (slides, looks, themes, fireplace files) must
  state their rollback limits in code output and docs. Never claim
  an asset change is undoable unless the provider can actually
  restore it.
- The lock PIN is out of scope by design: never read, send, log, or
  store PIN values.
- Keep personal environment data out of the repo: no LAN/VPN
  addresses, no machine-specific absolute paths, no real config
  snapshots. The test suite scans for this.

## Testing on real hardware

Note your launcher version and TV model in the PR. Anything that
changes the TV's visible state should be described with before/
after behaviour, and destructive provider methods (`pack_clear`,
`fire_clear`, `slides_clear`) are not accepted.
