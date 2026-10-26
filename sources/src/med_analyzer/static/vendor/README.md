# Vendored front-end libraries

## ECharts

- Name: Apache ECharts (npm package `echarts`)
- Version: 6.1.0
- File: `echarts.min.js`, copied unchanged from `dist/echarts.min.js` of the
  npm tarball
- Source: <https://registry.npmjs.org/echarts/-/echarts-6.1.0.tgz>
  (tarball sha1 `ae0f68590f5ebbd728d900907c27acde7c5456d1`, as published in the
  registry metadata)
- sha256 of `echarts.min.js`: `b66b25aeb4df84e33199dc21694014d336d222cbd9deb0e5a7c14bd6aa0d0fd0`
- License: Apache-2.0, see `echarts.LICENSE` and `echarts.NOTICE` (both copied
  unchanged from the package)

The bundle contains one `new Function` call, in the GeoJSON parser used by
map charts when it is given a string and `JSON.parse` is missing. The page
draws no maps and every browser has `JSON.parse`, so it never runs and the
Content-Security-Policy (`default-src 'self'`) does not need `unsafe-eval`.

To update: download the new tarball, replace the three files, and update the
version and the hash above; `tests/test_dynamics.py` checks the hash.
