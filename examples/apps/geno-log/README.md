# geno-log

`geno-log` is the release-gated `node-cli` reference app. It compiles to a
single JavaScript file, runs under Node.js, and summarizes a small set of
structured service events.

```bash
geno check examples/apps/geno-log
geno test examples/apps/geno-log
geno compile examples/apps/geno-log --target js -o /tmp/geno-log.js
node /tmp/geno-log.js
```
