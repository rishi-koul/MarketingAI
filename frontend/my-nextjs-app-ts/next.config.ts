import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  allowedDevOrigins: [
    '*.loca.lt', // Example: if your dev server runs on port 3000
    'http://127.0.0.1:3000', // Example: another common local address
    // Add any other origins from which you are making cross-origin requests
  ],
};

export default nextConfig;
