```markdown
# Genie Backend - AI Coding Assistant API

FastAPI-powered backend for an intelligent coding assistant with GitHub automation, AWS deployments, and AI-driven project generation.

## 🌟 Features

- 🤖 **LangGraph Chat Agents** - Intelligent conversational AI with tool calling and memory
- 🐙 **GitHub Automation** - Create repos, push code, manage branches, and upload projects
- ☁️ **AWS Deployment** - Automated static site deployment with Terraform and CloudFront
- 💳 **Stripe Integration** - Subscription management and payment processing
- 🗄️ **Supabase Storage** - Project file storage and PostgreSQL checkpoints
- ✨ **AI Project Generation** - Generate full React + Vite + Tailwind projects with Google Gemini
- 🔄 **Real-time Streaming** - Server-sent events for live chat responses
- 📚 **Documentation Generation** - Auto-generate README and user manuals for uploaded projects
- 🔐 **Role-based Access** - Free and premium tiers with admin dashboard
- 🧵 **Thread Management** - Persistent conversation history per user

## 🛠️ Tech Stack

| Category | Technologies |
|----------|-------------|
| **Framework** | FastAPI, Python 3.10+ |
| **AI/ML** | LangGraph, LangChain, Google Gemini (1.5 Flash), Groq (Qwen 3 32B) |
| **Database** | PostgreSQL (Supabase) with LangGraph checkpointing |
| **Storage** | Supabase Storage for project files |
| **Payments** | Stripe Checkout & Webhooks |
| **Infrastructure** | Terraform, AWS S3, CloudFront, Lambda@Edge |
| **Version Control** | GitHub API via PyGithub |
| **Auth** | Supabase Auth (GitHub OAuth) |

## 📋 Prerequisites

- Python 3.10 or higher
- PostgreSQL database (Supabase recommended)
- AWS account with access keys
- Google Gemini API key
- Groq API key
- Stripe account (test mode)
- GitHub account for OAuth
- Terraform installed (for AWS deployments)

## 🚀 Quick Start

### 1. Clone and Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/genie-backend.git
cd genie-backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Unix/MacOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

Copy .env.example to .env and configure your credentials:

```bash
cp .env.example .env
```

Required environment variables:

```env
# AWS
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key
AWS_DEFAULT_REGION=us-east-1
TERRAFORM_PATH=/path/to/terraform

# Supabase
SUPABASE_DB_URI=postgresql://user:password@host:port/database
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
SUPABASE_KEY=your_anon_key
SUPABASE_URL=https://your-project.supabase.co

# Stripe
STRIPE_SECRET_KEY_TEST=sk_test_your_key
STRIPE_TEST_PRICE_ID=price_your_id
STRIPE_WEBHOOK_SECRET_TEST=whsec_your_webhook_secret
FRONTEND_BASE_URL=http://localhost:5173

# AI Models
GOOGLE_API_KEY=your_google_api_key
GROQ_API_KEY=your_groq_api_key
GROQ_CHAT_MODEL=qwen/qwen3-32b
GROQ_TOOL_MODEL=qwen/qwen3-32b
MODEL_SUMMARIZER=openai/gpt-oss-20b
MODEL_DOCGEN=openai/gpt-oss-120b
```

### 3. Run the Server

```bash
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`

API documentation: `http://localhost:8000/docs`

## 📁 Project Structure

```
Backend/
├── api.py                 # Main FastAPI application and routes
├── agent.py              # LangGraph agent configuration
├── admin.py              # Admin dashboard endpoints
├── billing.py            # Stripe integration
├── db_utils.py           # Database utilities
├── projects.py           # Project management endpoints
├── requirements.txt      # Python dependencies
├── utils/
│   ├── code.py          # Project generation with Gemini
│   ├── tools.py         # LangGraph tools (GitHub, AWS, docs)
│   ├── nodes.py         # LangGraph chat node with summarization
│   ├── state.py         # State definitions
│   ├── doc_gen_functionality.py  # Documentation generation
│   ├── supabase_iteration.py     # Supabase file operations
│   ├── main.tf          # Terraform AWS configuration
│   └── variables.tf     # Terraform variables
└── .env.example         # Environment template
```

## 🔌 API Endpoints

### Chat & Threads
- `POST /chat/stream` - Stream chat responses (NDJSON)
- `GET /threads` - List user's conversation threads
- `POST /threads` - Create new thread
- `GET /threads/{thread_id}/messages` - Get thread history

### Projects
- `GET /projects` - List generated projects
- `GET /projects/{project_id}/file-tree` - Get project file structure
- `GET /projects/{project_id}/file-content` - Read file content
- `POST /projects/{project_id}/upload-to-github` - Push to GitHub

### Billing
- `POST /billing/create-checkout-session` - Start Stripe checkout
- `POST /billing/webhook` - Handle Stripe webhooks

### Admin (requires admin role)
- `GET /admin/users` - List all users
- `PATCH /admin/users/{user_id}/plan` - Update user plan
- `GET /admin/metrics` - System metrics
- `GET /admin/pricing` - Manage pricing plans
- `PATCH /admin/pricing/{plan_id}` - Update pricing

### Public
- `GET /pricing` - Get active pricing plan

## 🤖 AI Tools Available

### Free Tier
- Generate React projects with Gemini
- List generated projects
- Generate project documentation

### Premium Tier (GitHub & AWS tools)
- GitHub username retrieval
- Repository management (create, delete, list)
- Branch operations (create, delete, rename, switch)
- File operations (create, read, delete)
- Upload projects to GitHub
- Deploy React sites to AWS with Terraform

## 🔒 Security Features

- Environment-based secrets management
- Role-based access control (user/admin)
- Plan-based feature gating (free/premium)
- Stripe webhook signature verification
- Supabase Row Level Security integration
- PostgreSQL prepared statements

## 🗄️ Database Schema

The application uses Supabase PostgreSQL with:
- `user_profiles` - User data, plans, roles
- `pricing_plans` - Pricing configuration
- LangGraph checkpoint tables (auto-created)

## 🧪 Development

### Run with auto-reload:
```bash
uvicorn api:app --reload
```

### Test Stripe webhooks locally:
```bash
stripe listen --forward-to localhost:8000/billing/webhook
```

### Run tests:
```bash
pytest
```

## 📦 Deployment

### Production checklist:
- [ ] Update `FRONTEND_BASE_URL` to production URL
- [ ] Use production Stripe keys
- [ ] Enable HTTPS
- [ ] Configure CORS origins
- [ ] Set up monitoring and logging
- [ ] Use production database
- [ ] Rotate all API keys

### Deploy to cloud:
```bash
# AWS, Google Cloud, or Azure
# Use your preferred deployment method
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request


## 🙏 Acknowledgments

- [LangGraph](https://github.com/langchain-ai/langgraph) for agent orchestration
- [FastAPI](https://fastapi.tiangolo.com/) for the web framework
- [Supabase](https://supabase.com/) for database and storage
- [Google Gemini](https://ai.google.dev/) for project generation
- [Groq](https://groq.com/) for fast inference
- [Stripe](https://stripe.com/) for payments

## 📧 Support

For issues and questions:
- Open an issue on GitHub
- Email: meghanigorav@example.com

---

Built with ❤️ using FastAPI and LangGraph
```
