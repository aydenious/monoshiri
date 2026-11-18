import { createBackendProxy } from "@/lib/server/backend-proxy";

export const dynamic = "force-dynamic";

const proxy = createBackendProxy("/auth");

export {
	proxy as GET,
	proxy as POST,
	proxy as PUT,
	proxy as PATCH,
	proxy as DELETE,
	proxy as OPTIONS,
	proxy as HEAD,
};
