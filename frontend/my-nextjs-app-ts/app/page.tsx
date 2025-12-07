"use client";
import Link from "next/link";

export default function HomePage() {
  return (
    <main className="min-h-screen flex items-center justify-center p-6">
      <div className="max-w-md text-center space-y-4">
        <h1 className="text-2xl font-semibold">Welcome</h1>
        <p className="text-gray-600">Connect your Instagram (Business/Creator) account to enable DMs & publishing.</p>
        <Link
          href="/connect"
          className="inline-flex items-center justify-center rounded-xl px-4 py-2 bg-blue-600 text-white hover:bg-blue-700"
        >
          Go to Connect
        </Link>
      </div>
    </main>
  );
}
