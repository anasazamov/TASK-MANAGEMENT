"""One Django ASGI process serves pages, HTTP APIs and realtime voice."""
import argparse
import uvicorn

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    print(f'Topshiriq nazorati: http://{args.host}:{args.port}', flush=True)
    uvicorn.run('config.asgi:application', host=args.host, port=args.port, workers=1,
                ws='websockets-sansio', ws_max_size=65536, lifespan='off', proxy_headers=False,
                access_log=False)
