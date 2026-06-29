/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: false, // r3f + SSE: avoid dev double-invoke
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  eslint: { ignoreDuringBuilds: true },
  typescript: { ignoreBuildErrors: true },
};
export default nextConfig;
