import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentCore Trading Strategy Backtester",
  description: "Create and backtest trading strategies using Amazon Bedrock AgentCore",
};

import { BacktestProvider } from "@/lib/BacktestContext";
import Disclaimer from "@/components/Disclaimer";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-dark-primary flex flex-col">
        <BacktestProvider>
          <div className="flex-1">{children}</div>
          {/* Site-wide, so a visitor landing straight on a results page still
              sees it — the README is not where anyone reads a disclaimer. */}
          <Disclaimer />
        </BacktestProvider>
      </body>
    </html>
  );
}
