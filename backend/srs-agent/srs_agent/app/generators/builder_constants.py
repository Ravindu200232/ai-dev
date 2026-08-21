"""Shared builder-handoff constants."""

KINDS = ("page", "create", "edit", "delete", "list", "feature")
ALLOWED_PACKAGES = (
    "react", "react-dom", "next", "mongoose", "lucide-react", "bcryptjs",
    "jose", "tailwind", "tailwindcss",
)
_FORBIDDEN = (
    "zod", "react-hook-form", "next-auth", "prisma", "postgres", "postgresql",
    "mysql", "sqlite", "redux", "zustand", "graphql", "trpc", "stripe",
    "firebase", "supabase", "express", "fastapi", "django", "material-ui",
    "mui", "chakra", "bootstrap", "styled-components", "axios", "sequelize",
    "typeorm", "socket.io", "auth0", "clerk",
)
MAX_WORDS = None
MAX_CHARS = None
HANDOFF_VERSION = 4
