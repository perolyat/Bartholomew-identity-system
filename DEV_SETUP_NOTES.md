Local Development Setup - Copy/Paste Commands
Terminal 1: Backend

# Install Encore CLI (choose one)
brew install encoredev/tap/encore          # macOS
npm install -g encore.dev                   # All platforms

# Set Clerk secret (if not already set by Leap)
encore secret set --dev ClerkSecretKey
# Paste: sk_test_YOUR_CLERK_SECRET_KEY

# Start backend (auto-creates database + runs migrations)
encore run
Backend runs on: http://localhost:4000
Dev dashboard: http://localhost:9400

Terminal 2: Frontend

cd frontend
npm install
npm run dev
Frontend runs on: http://localhost:5173

Database Setup
Automatic: When you run encore run, it:

✅ Starts PostgreSQL in Docker
✅ Creates database
✅ Runs migrations (creates all tables)
Inspect database:


encore db shell db --env=local

\dt                              -- List tables
SELECT * FROM users;             -- View users
SELECT * FROM user_preferences;  -- View preferences
\q                               -- Exit
Create Test User
Open http://localhost:5173/login
Click "Sign up"
Enter email: test@example.com
Set password
Complete Clerk signup
User auto-created in DB on first login
Reset Everything

# Reset database
encore db reset db --env=local

# Restart backend
encore run
That's it! Backend + frontend + database all running locally.
