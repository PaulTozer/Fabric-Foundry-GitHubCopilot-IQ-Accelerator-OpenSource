/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  serverExternalPackages: ['pdfjs-dist', '@napi-rs/canvas'],
  outputFileTracingIncludes: {
    '/api/upload': [
      './node_modules/pdfjs-dist/legacy/build/*',
      './node_modules/pdfjs-dist/standard_fonts/*',
      './node_modules/@napi-rs/canvas*/**/*',
    ],
  },
  images: { unoptimized: true },
  devIndicators: false,
};
export default nextConfig;
