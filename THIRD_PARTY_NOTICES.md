# Third-party viewer

The React chart preview integrates **chart-preview 1.3.0**, by Geo / Geomitron.

- Source and documentation: https://github.com/Geomitron/chart-preview
- npm package: https://www.npmjs.com/package/chart-preview
- License: MIT (declared by the upstream package and README).

The component is installed as a dependency, rather than copied into project source.
Its dependencies include Three.js, scan-chart, parse-sng, and eventemitter3;
retain their bundled license notices when distributing dependencies.

Upstream's texture loader requests assets from https://static.enchor.us/.
Those remotely served assets are not copied into this repository. The package's
MIT declaration is not a separate claim about ownership of those assets.
