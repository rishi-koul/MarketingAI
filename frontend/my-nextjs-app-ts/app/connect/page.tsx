"use client";
import React, { useEffect, useMemo, useState } from "react";

type PageItem = {
    id: string;
    name: string;
    category?: string;
    has_ig?: boolean;
    ig_id?: string | null;
};

type ExchangeResp = { ok: true } | { ok: false; error: string };
type PagesResp = { ok: true; pages: PageItem[] } | { ok: false; error: string };
type SubscribeResp =
    | { ok: true; page_id: string; ig_id?: string | null }
    | { ok: false; error: string };

const FB_SCOPES = [
    "pages_show_list",
    "pages_manage_metadata",
    "pages_messaging",
    "instagram_basic",
    "instagram_manage_messages",
    "instagram_content_publish",
];

const cls = (...arr: (string | false | null | undefined)[]) =>
    arr.filter(Boolean).join(" ");

function randomState(len = 24) {
    const chars =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    let s = "";
    for (let i = 0; i < len; i++) s += chars[Math.floor(Math.random() * chars.length)];
    return s;
}

export default function ConnectInstagramPage() {
    const [ui, setUi] = useState<
        | "idle"
        | "redirecting"
        | "exchanging"
        | "listing"
        | "subscribing"
        | "done"
        | "error"
    >("idle");
    const [error, setError] = useState<string | null>(null);
    const [pages, setPages] = useState<PageItem[]>([]);
    const [selected, setSelected] = useState<PageItem | null>(null);
    const [result, setResult] = useState<{ page_id?: string; ig_id?: string | null }>(
        {}
    );

    const appId = process.env.NEXT_PUBLIC_FB_APP_ID || "";
    const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
    const redirectUri = useMemo(() => {
        if (typeof window === "undefined") return process.env.NEXT_PUBLIC_REDIRECT_URI || "";
        const url = new URL(window.location.href);
        url.search = ""; // strip any ?code=… from the clean value
        return process.env.NEXT_PUBLIC_REDIRECT_URI || url.toString();
    }, []);

    // On return from OAuth (?code=...&state=...), exchange code and then list pages
    useEffect(() => {
        if (typeof window === "undefined") return;
        const params = new URLSearchParams(window.location.search);
        const code = params.get("code");
        const state = params.get("state");
        if (!code) return;

        const expected = sessionStorage.getItem("fb_oauth_state");
        if (!state || !expected || state !== expected) {
            setError("State mismatch. Start over.");
            setUi("error");
            return;
        }

        (async () => {
            setUi("exchanging");
            try {
                const r = await fetch(`${API_BASE}/oauth/exchange`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    credentials: "include",
                    body: JSON.stringify({ code, redirect_uri: redirectUri }),
                });
                const data = (await r.json()) as ExchangeResp;
                if (!r.ok || !("ok" in data) || data.ok !== true) {
                    throw new Error((data as any)?.error || `Exchange failed (${r.status})`);
                }
                // clean URL
                const clean = new URL(window.location.href);
                clean.search = "";
                window.history.replaceState({}, "", clean.toString());
                await loadPages();
            } catch (e: any) {
                setError(e?.message || "OAuth exchange failed");
                setUi("error");
            }
        })();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [redirectUri]);

    async function loadPages() {
        setUi("listing");
        setError(null);
        try {
            const r = await fetch(`${API_BASE}/facebook/pages`, { credentials: "include" });
            const data = (await r.json()) as PagesResp;
            if (!r.ok || !data.ok) throw new Error((data as any)?.error || "Failed to load Pages");
            setPages(data.pages || []);
        } catch (e: any) {
            setError(e?.message || "Could not fetch Pages");
            setUi("error");
        }
    }

    function buildAuthUrl() {
        if (!appId) throw new Error("Missing NEXT_PUBLIC_FB_APP_ID");
        if (!redirectUri) throw new Error("Missing redirect URL");
        const state = randomState();
        sessionStorage.setItem("fb_oauth_state", state);
        const url = new URL("https://www.facebook.com/dialog/oauth");
        url.searchParams.set("client_id", appId);
        url.searchParams.set("redirect_uri", redirectUri);
        url.searchParams.set("state", state);
        url.searchParams.set("response_type", "code");
        url.searchParams.set("scope", FB_SCOPES.join(","));
        url.searchParams.set("auth_type", "rerequest");
        return url.toString();
    }

    async function startConnect() {
        try {
            setUi("redirecting");
            window.location.href = buildAuthUrl();
        } catch (e: any) {
            setError(e?.message || "Could not start OAuth");
            setUi("error");
        }
    }

    async function subscribeToPage(p: PageItem) {
        setSelected(p);
        setUi("subscribing");
        setError(null);
        try {
            const r = await fetch(`${API_BASE}/facebook/pages/${encodeURIComponent(p.id)}/subscribe`, {
                method: "POST",
                credentials: "include",
            });
            const data = (await r.json()) as SubscribeResp;
            if (!r.ok || !data.ok) throw new Error((data as any)?.error || "Subscription failed");
            setResult({ page_id: data.page_id, ig_id: data.ig_id });
            setUi("done");
        } catch (e: any) {
            setError(e?.message || "Could not subscribe Page");
            setUi("error");
        }
    }

    return (
        <div className="min-h-screen bg-gray-50 flex items-center justify-center p-6">
            <div className="w-full max-w-2xl">
                <div className="bg-white shadow-lg rounded-2xl p-6 md:p-8">
                    <header className="flex items-center justify-between">
                        <h1 className="text-2xl font-semibold">Creator Connect</h1>
                        <span className="text-xs text-gray-500">POC • IG DMs + Publish</span>
                    </header>

                    <p className="mt-3 text-gray-600">
                        Sign in with Facebook, pick a Page, and we’ll subscribe it so Instagram DMs reach your webhook.
                    </p>

                    {/* Step 1 – Connect */}
                    {ui === "idle" && (
                        <div className="mt-6">
                            <button
                                onClick={startConnect}
                                className={cls(
                                    "w-full md:w-auto inline-flex items-center justify-center gap-2",
                                    "rounded-2xl px-5 py-3 font-medium shadow-sm",
                                    "bg-blue-600 text-white hover:bg-blue-700 transition"
                                )}
                            >
                                <svg aria-hidden viewBox="0 0 24 24" className="w-5 h-5 fill-current">
                                    <path d="M22 12a10 10 0 1 0-11.6 9.9v-7H7v-3h3.4V9.5c0-3.3 2-5.2 5-5.2 1.4 0 2.8.25 2.8.25v3.1H16c-1.6 0-2.1 1-2.1 2v2.4H18l-.6 3h-3.5v7A10 10 0 0 0 22 12z" />
                                </svg>
                                Connect Instagram (via Facebook)
                            </button>
                            <div className="mt-3 text-xs text-gray-500">
                                Scopes: {FB_SCOPES.join(", ")}
                            </div>
                        </div>
                    )}

                    {ui === "redirecting" && (
                        <div className="mt-6 text-gray-600">Redirecting to Facebook Login…</div>
                    )}
                    {ui === "exchanging" && (
                        <div className="mt-6 text-gray-600">Exchanging authorization code…</div>
                    )}

                    {/* Step 2 – List Pages */}
                    {ui === "listing" && (
                        <div className="mt-6">
                            <h2 className="text-lg font-semibold mb-3">Select a Facebook Page</h2>
                            {pages.length === 0 ? (
                                <div className="text-gray-600">
                                    No Pages found for this user or permissions. Ensure{" "}
                                    <code className="mx-1 px-1 rounded bg-gray-100">pages_show_list</code> was granted.
                                </div>
                            ) : (
                                <ul className="space-y-3">
                                    {pages.map((p) => (
                                        <li
                                            key={p.id}
                                            className="flex items-center justify-between border rounded-xl p-4 hover:shadow-sm"
                                        >
                                            <div>
                                                <div className="font-medium">{p.name}</div>
                                                <div className="text-xs text-gray-500">
                                                    {p.category || "Page"} • {p.has_ig ? `IG linked${p.ig_id ? ` (#${p.ig_id})` : ""}` : "No IG linked"}
                                                </div>
                                            </div>
                                            <button
                                                onClick={() => subscribeToPage(p)}
                                                className={cls(
                                                    "inline-flex items-center justify-center rounded-xl px-4 py-2",
                                                    "bg-gray-900 text-white hover:bg-black transition"
                                                )}
                                            >
                                                Subscribe
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    )}

                    {ui === "subscribing" && (
                        <div className="mt-6 text-gray-600">
                            Subscribing <span className="font-medium">{selected?.name}</span> to messages…
                        </div>
                    )}

                    {/* Done */}
                    {ui === "done" && (
                        <div className="mt-6 rounded-xl border bg-green-50 p-4">
                            <div className="font-medium text-green-800">Connected!</div>
                            <div className="text-sm text-green-800 mt-1">
                                Page <span className="font-mono">{result.page_id}</span>{" "}
                                {result.ig_id ? (
                                    <>
                                        is linked to IG Business <span className="font-mono">{result.ig_id}</span>.
                                    </>
                                ) : (
                                    <>has no linked IG Business account.</>
                                )}
                            </div>
                            <div className="text-xs text-green-800 mt-2">
                                We subscribed the Page so DMs hit your webhook. You can manage this in Page settings.
                            </div>
                        </div>
                    )}

                    {/* Error */}
                    {ui === "error" && (
                        <div className="mt-6 rounded-xl border bg-red-50 p-4">
                            <div className="font-medium text-red-800">Something went wrong</div>
                            <div className="text-sm text-red-800 mt-1">{error}</div>
                            <div className="mt-3">
                                <button
                                    onClick={() => {
                                        setError(null);
                                        setUi("idle");
                                    }}
                                    className="inline-flex items-center justify-center rounded-xl px-4 py-2 bg-red-600 text-white hover:bg-red-700 transition"
                                >
                                    Try again
                                </button>
                            </div>
                        </div>
                    )}

                    <footer className="mt-8 border-t pt-4 text-xs text-gray-500 space-y-1">
                        <p>This page builds the OAuth URL client-side; the backend exchanges the code and stores tokens in a secure session cookie.</p>
                        <p>Flow: <code>/oauth/exchange</code> → <code>/facebook/pages</code> → <code>/facebook/pages/:id/subscribe</code>.</p>
                    </footer>
                </div>
            </div>
        </div>
    );
}
