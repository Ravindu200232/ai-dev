export const dynamic = "force-dynamic";

export async function GET() {
  return Response.json({ status: "ok", service: "nextjs", timestamp: new Date().toISOString() });
}
