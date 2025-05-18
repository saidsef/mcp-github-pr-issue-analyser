from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="mcp-saidsef-resume",
    version="1.0.1",
    author="saidsef",
    description="MCP Said Sef Resume and Socials",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://saidsef.co.uk/saidsef/mcp-said-sef-resume",
    packages=find_packages(),
    py_modules=["github_integration", "issues_pr_analyser"],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: Apache 2",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.12",
    install_requires=[
        "requests",
        "mcp[cli]",
    ],
    entry_points={
        "console_scripts": [
            "mcp-github-pr-issue-analyser=issues_pr_analyser:main",
        ],
    },
)
