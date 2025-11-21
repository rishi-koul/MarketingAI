# MarketingAI

copy .env into each folder backend and frontend


terminal 1 in backend folder
python -m venv .venv && source .venv/bin/activate

pip install fastapi uvicorn python-dotenv httpx

uvicorn app:app --port 8080 --reload

terminal 2:

ngrok config add-authtoken [ASK_ME]

ngrok http 8080


terminal 3 in frontend:

npm run dev
