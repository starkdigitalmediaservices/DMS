#!/bin/bash

###############################################################################
# PostgreSQL + Redis Installation Script for DMS
#
# OS        : Ubuntu 22.04 / 24.04
# Author    : AI Assistant
#
# This script:
#   ✓ Updates the system
#   ✓ Installs PostgreSQL and the pgvector extension
#   ✓ Installs Redis
#   ✓ Creates the database user and database as per project specs
#   ✓ Creates the necessary tables ('documents', 'chunks') with pgvector
#   ✓ Configures password authentication
#   ✓ Enables and tests the services
#
# Run:
#   chmod +x scripts/setup_postgres_redis.sh
#   ./scripts/setup_postgres_redis.sh
#
###############################################################################

set -e

echo "=============================================="
echo " DMS PostgreSQL + Redis Installer"
echo "=============================================="

#############################
# Configuration
#############################

DB_NAME="docsearch"
DB_USER="docsearch"
DB_PASSWORD="reset123" # Match this with your .env file

#############################
# Update Packages
#############################

echo
echo "Updating package list..."
sudo apt update

#############################
# Install PostgreSQL & pgvector
#############################

echo
echo "Installing PostgreSQL and pgvector..."

sudo apt install -y postgresql postgresql-contrib
# Add pgvector repository and install
sudo apt install -y wget ca-certificates
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
sudo apt-get update
sudo apt-get install -y postgresql-18-pgvector

#############################
# Install Redis
#############################

echo
echo "Installing Redis..."
sudo apt install -y redis-server

#############################
# Enable Services
#############################

echo
echo "Starting and enabling services..."
sudo systemctl enable --now postgresql
sudo systemctl restart postgresql
sudo systemctl enable --now redis-server
sudo systemctl restart redis-server

#############################
# Detect PostgreSQL Version
#############################

PG_VERSION=$(ls /etc/postgresql | sort -V | tail -n1)
echo
echo "Detected PostgreSQL Version: $PG_VERSION"

#############################
# Configure Authentication
#############################

PG_HBA="/etc/postgresql/$PG_VERSION/main/pg_hba.conf"
echo
echo "Configuring password authentication in $PG_HBA..."

# Use scram-sha-256 for secure password authentication
sudo sed -i \
's/^\(local\s\+all\s\+all\s\+\)peer/\1scram-sha-256/' \
"$PG_HBA"
sudo systemctl restart postgresql

#############################
# Create Database User and Database
#############################

echo
echo "Creating PostgreSQL user '$DB_USER' and database '$DB_NAME'..."

sudo -u postgres psql <<EOF
DO
\$\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$DB_USER') THEN
      CREATE USER $DB_USER WITH PASSWORD '$DB_PASSWORD';
   END IF;
END
\$\$;

SELECT 'CREATE DATABASE $DB_NAME OWNER $DB_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='$DB_NAME')\gexec
EOF

#############################
# Grant Privileges & Create Extension
#############################

echo
echo "Granting privileges and creating pgvector extension..."

sudo -u postgres psql -d $DB_NAME <<EOF
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
CREATE EXTENSION IF NOT EXISTS vector;
EOF

#############################
# Create Tables
#############################

echo
echo "Creating database tables ('documents' and 'chunks')..."

export PGPASSWORD=$DB_PASSWORD
psql -h localhost -U $DB_USER -d $DB_NAME <<EOF
CREATE TABLE IF NOT EXISTS documents (
    document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    s3_path VARCHAR(255) NOT NULL,
    document_name VARCHAR(255),
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES documents(document_id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL,
    content TEXT,
    embedding VECTOR(1536),
    metadata JSONB,
    page_number INTEGER,
    s3_path VARCHAR(255)
);

-- Grant permissions to the app user
GRANT SELECT, INSERT, UPDATE, DELETE ON documents, chunks TO $DB_USER;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO $DB_USER;

EOF
unset PGPASSWORD

#############################
# Test Connections
#############################

echo
echo "Testing PostgreSQL connection..."
export PGPASSWORD=$DB_PASSWORD
psql -h localhost -U $DB_USER -d $DB_NAME -c "SELECT version();"
unset PGPASSWORD

echo
echo "Testing Redis connection..."
redis-cli ping

#############################
# Final Output
#############################

echo
echo "========================================"
echo " Installation Complete"
echo "========================================"
echo
echo "Database Name : $DB_NAME"
echo "Username      : $DB_USER"
echo "Password      : $DB_PASSWORD (Ensure this matches your .env)"
echo
echo "PostgreSQL Connection URL:"
echo "postgresql+asyncpg://$DB_USER:$DB_PASSWORD@localhost:5432/$DB_NAME"
echo
echo "Redis Connection URL:"
echo "redis://localhost:6379/0"
echo
echo "Done."