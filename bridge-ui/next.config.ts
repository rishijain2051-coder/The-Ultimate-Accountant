import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Static export: `npm run build` emits bridge-ui/out, which the app's one
  // server serves under /bridge (app/main.py), beside Reconciliation and
  // Exports at /. One port, one process, nothing to orchestrate on the
  // user's machine.
  output: "export",
  basePath: "/bridge",
  images: { unoptimized: true },
  trailingSlash: true,
};

export default nextConfig;
