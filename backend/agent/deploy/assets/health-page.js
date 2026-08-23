export default function handler(_request, response) {
  response.status(200).json({ status: "ok", service: "nextjs", timestamp: new Date().toISOString() });
}
