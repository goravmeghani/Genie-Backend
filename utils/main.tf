terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# -------------------------------
#  S3 BUCKET (Static Site Hosting)
# -------------------------------
resource "aws_s3_bucket" "static_site" {
  bucket        = var.bucket_name
  force_destroy = true

  tags = {
    Name        = var.bucket_name
    Environment = "dev"
  }
}

resource "aws_s3_bucket_public_access_block" "public_access" {
  bucket = aws_s3_bucket.static_site.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "website_config" {
  bucket = aws_s3_bucket.static_site.id

  index_document {
    suffix = "index.html"
  }

  error_document {
    key = "index.html"
  }
}

resource "aws_s3_bucket_policy" "public_policy" {
  bucket = aws_s3_bucket.static_site.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicRead"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.static_site.arn}/*"
      }
    ]
  })
}

# -------------------------------
# IAM ROLE FOR CODEBUILD
# -------------------------------
resource "aws_iam_role" "codebuild_role" {
  name = "${var.bucket_name}-codebuild-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Principal = {
        Service = "codebuild.amazonaws.com"
      },
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "codebuild_policy" {
  name = "${var.bucket_name}-codebuild-policy"
  role = aws_iam_role.codebuild_role.id

  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:*",
          "logs:*"
        ],
        Resource = "*"
      }
    ]
  })
}

# -------------------------------
# CODEBUILD PROJECT (FOR VITE)
# -------------------------------
resource "aws_codebuild_project" "build_project" {
  name         = "${var.bucket_name}-build"
  service_role = aws_iam_role.codebuild_role.arn
  description  = "Build and deploy Vite app"

  artifacts {
    type = "NO_ARTIFACTS"
  }

  environment {
    compute_type = "BUILD_GENERAL1_SMALL"
    image        = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
    type         = "LINUX_CONTAINER"

    environment_variable {
      name  = "S3_BUCKET_NAME"
      value = var.bucket_name
    }
  }

  source {
    type            = "GITHUB"
    location        = var.github_repo
    git_clone_depth = 1

    buildspec = <<EOF
version: 0.2

phases:
  install:
    runtime-versions:
      nodejs: 20
    commands:
      - echo "Installing dependencies"
      - npm install

  build:
    commands:
      - echo "Fixing Vite file permissions"
      - chmod +x node_modules/.bin/vite || true
      - echo "Running Vite build"
      - npm run build

  post_build:
    commands:
      - echo "Deploying dist/ to S3"
      - aws s3 sync dist/ s3://$S3_BUCKET_NAME --delete

EOF
  }

  tags = {
    Environment = "dev"
  }
}

# -------------------------------
# OUTPUT
# -------------------------------
output "bucket_url" {
  value       = "http://${var.bucket_name}.s3-website-${var.aws_region}.amazonaws.com"
  description = "Static site URL"
}