import "./auth.css";

/** Minimal shell for /login and /register: just the dark palette, no site chrome. */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
	return <main className="ss-home min-h-screen">{children}</main>;
}
