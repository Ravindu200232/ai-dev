# Starts the HTTP, WebSocket and sidecar services.
def start_http():
    try:

        httpd = _UIServer((bind_host(), UI_PORT), UIHandler)
        print(f"HTTP server listening on 127.0.0.1:{UI_PORT}")
        httpd.serve_forever()
    except Exception as e:
        print(f"HTTP server failed: {e}")


async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()

    threading.Thread(target=MONGO.ensure_running, daemon=True).start()
    threading.Thread(target=start_srs_api, daemon=True).start()
    threading.Thread(target=start_deploy_api, daemon=True).start()
    threading.Thread(target=start_http, daemon=True).start()
    print(f"\n{'━'*46}")
    print(f"  ⚡ AgentForge v1.0.0 Starting...")
    print(f"  ⚡ UI Server   →  http://127.0.0.1:{UI_PORT}")
    print(f"  🔌 WebSocket   →  ws://127.0.0.1:{WS_PORT}")
    print(f"  📄 SRS agent   →  http://127.0.0.1:{SRS_PORT}")
    print(f"  🚀 Deploy      →  http://127.0.0.1:{DEPLOY_PORT}")
    print(f"  🧠 Refine      :  {DEFAULT_REFINE}")
    print(f"  🏗️  Build       :  {DEFAULT_BUILD}")
    print(f"  📝 Local development mode")
    print(f"{'━'*46}\n")
    async with websockets.serve(ws_handler, bind_host(), WS_PORT):
        await asyncio.Future()


def shutdown_all():
    print("\n🛑 Shutting down AgentForge backend...")

    if active_vite.get("proc"):
        try:
            _stop_dev_proc()
            print("   ✅ Dev server stopped")
        except:
            pass

    try:
        MONGO.stop()
    except:
        pass

    try:
        stop_model(DEFAULT_REFINE)
        stop_model(DEFAULT_BUILD)
        print("   ✅ Ollama models unloaded")
    except:
        pass

atexit.register(shutdown_all)

def handle_signal(sig, frame):
    shutdown_all()
    sys.exit(0)

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n⛔ Stopped.")
        if active_vite["proc"]:
            try: active_vite["proc"].terminate()
            except: pass
