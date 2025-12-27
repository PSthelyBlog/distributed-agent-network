#!/bin/bash
#
# Development Environment Setup Script
#
# Creates a Python virtual environment and installs dependencies.
#

set -e

SCRIPT_DIR=$(dirname "$(realpath "$0")")
PROJECT_DIR=$(dirname "$SCRIPT_DIR")
VENV_DIR="$PROJECT_DIR/.venv"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Setting up development environment...${NC}"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is required but not installed."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${BLUE}Creating virtual environment at $VENV_DIR...${NC}"
    python3 -m venv "$VENV_DIR"
else
    echo -e "${GREEN}Virtual environment already exists at $VENV_DIR${NC}"
fi

# Activate virtual environment
echo -e "${BLUE}Activating virtual environment...${NC}"
source "$VENV_DIR/bin/activate"

# Upgrade pip
echo -e "${BLUE}Upgrading pip...${NC}"
pip install --upgrade pip

# Install dependencies
echo -e "${BLUE}Installing dependencies...${NC}"
pip install -r "$PROJECT_DIR/requirements.txt"

echo ""
echo -e "${GREEN}Development environment setup complete!${NC}"
echo ""
echo "To activate the virtual environment, run:"
echo -e "  ${BLUE}source $VENV_DIR/bin/activate${NC}"
echo ""
echo "To run tests:"
echo -e "  ${BLUE}pytest tests/ -v${NC}"
echo ""
