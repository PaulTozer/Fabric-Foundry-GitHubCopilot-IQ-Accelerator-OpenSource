import type { Metadata } from 'next';
import './globals.css';


export const metadata: Metadata = {
  title: 'Fabric Foundry GitHubCopilot IQ Accelerator',
  description: 'Fabric knowledge, Copilot SDK BYOK, Council and Work IQ with MCP tools and document citations.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="font-sans">{children}</body>
    </html>
  );
}
