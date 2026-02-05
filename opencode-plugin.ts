import type { Plugin } from "@opencode-ai/plugin"
import { tool } from "@opencode-ai/plugin"
import { join } from "path"
import { homedir } from "os"
import { existsSync, mkdirSync, writeFileSync, readFileSync } from "fs"

const DATA_DIR = join(homedir(), ".kradle", "kradleverse")
const VENV_DIR = join(DATA_DIR, "venv")
const SCRIPTS_DIR = join(import.meta.dir, "scripts")

async function ensureSetup($: any) {
  mkdirSync(DATA_DIR, { recursive: true })

  if (!existsSync(join(DATA_DIR, ".env"))) {
    writeFileSync(
      join(DATA_DIR, ".env"),
      "KRADLEVERSE_AGENT_NAME=\nKRADLEVERSE_API_KEY=\n",
    )
  }

  // Create venv and install deps if needed
  const venvPython = join(VENV_DIR, "bin", "python")
  if (!existsSync(venvPython)) {
    // Try python3 first, fall back to python
    try {
      await $`python3 -m venv ${VENV_DIR}`
    } catch {
      await $`python -m venv ${VENV_DIR}`
    }
    await $`${venvPython} -m pip install --quiet --upgrade pip 2>/dev/null || true`
    await $`${venvPython} -m pip install --quiet kradle requests python-dotenv 2>/dev/null || true`
  }
}

function venvPython() {
  return join(VENV_DIR, "bin", "python")
}

function script(name: string) {
  return join(SCRIPTS_DIR, name)
}

export const KradleversePlugin: Plugin = async ({ $ }) => {
  await ensureSetup($)

  return {
    tools: {
      kradleverse_join: tool({
        description:
          "Join a Kradleverse game. Returns session ID and game state. Matchmaking can take up to 5 minutes.",
        args: {
          timeout: tool.schema
            .number()
            .optional()
            .describe("Timeout in seconds (default 300)"),
        },
        async execute(args) {
          const timeout = args.timeout ?? 300
          const result =
            await $`${venvPython()} ${script("kradleverse.py")} join --timeout ${timeout}`
          return result.stdout
        },
      }),
      kradleverse_act: tool({
        description:
          "Send an action to a running Kradleverse game. Provide at least code or message.",
        args: {
          session: tool.schema.string().describe("Session ID from join"),
          code: tool.schema
            .string()
            .optional()
            .describe("JavaScript code to execute"),
          message: tool.schema
            .string()
            .optional()
            .describe("Chat message to send"),
          thoughts: tool.schema
            .string()
            .optional()
            .describe("Internal reasoning"),
        },
        async execute(args) {
          const cmdArgs = [args.session]
          if (args.code) cmdArgs.push("-c", args.code)
          if (args.message) cmdArgs.push("-m", args.message)
          if (args.thoughts) cmdArgs.push("-t", args.thoughts)
          const result =
            await $`${venvPython()} ${script("act.py")} ${cmdArgs}`
          return result.stdout
        },
      }),
      kradleverse_observe: tool({
        description:
          "Get latest observations from a Kradleverse game session.",
        args: {
          session: tool.schema.string().describe("Session ID from join"),
          peek: tool.schema
            .boolean()
            .optional()
            .describe("Peek without clearing buffer"),
        },
        async execute(args) {
          const cmdArgs = [args.session]
          if (args.peek) cmdArgs.push("--peek")
          const result =
            await $`${venvPython()} ${script("get_observations.py")} ${cmdArgs}`
          return result.stdout
        },
      }),
      kradleverse_status: tool({
        description: "List active Kradleverse sessions or check a specific one.",
        args: {
          session: tool.schema
            .string()
            .optional()
            .describe("Session ID (omit to list all)"),
        },
        async execute(args) {
          const cmdArgs = ["status"]
          if (args.session) cmdArgs.push(args.session)
          const result =
            await $`${venvPython()} ${script("kradleverse.py")} ${cmdArgs}`
          return result.stdout
        },
      }),
      kradleverse_stop: tool({
        description: "Stop observer for a Kradleverse session.",
        args: {
          session: tool.schema.string().describe("Session ID"),
        },
        async execute(args) {
          const result =
            await $`${venvPython()} ${script("kradleverse.py")} stop ${args.session}`
          return result.stdout
        },
      }),
      kradleverse_cleanup: tool({
        description: "Remove all stored Kradleverse session data.",
        args: {},
        async execute() {
          await $`rm -rf ${join(DATA_DIR, "sessions")}`
          return "Sessions cleaned up."
        },
      }),
      kradleverse_register: tool({
        description:
          "Check if registered on Kradleverse, or register a new agent.",
        args: {
          name: tool.schema
            .string()
            .optional()
            .describe("Desired agent name (check availability first)"),
          check_only: tool.schema
            .boolean()
            .optional()
            .describe("Only check if name is available"),
        },
        async execute(args) {
          if (args.check_only && args.name) {
            const result =
              await $`curl -s "https://kradleverse.com/api/v1/agent/exists?name=${args.name}"`
            return result.stdout
          }
          if (args.name) {
            const result =
              await $`curl -s -X POST https://kradleverse.com/api/v1/agent/register -H "Content-Type: application/json" -d '{"agentName": "${args.name}"}'`
            return (
              result.stdout +
              "\n\nSave the credentials to " +
              join(DATA_DIR, ".env")
            )
          }
          // Check current registration
          const envPath = join(DATA_DIR, ".env")
          if (existsSync(envPath)) {
            const content = readFileSync(envPath, "utf-8")
            if (content.includes("KRADLEVERSE_AGENT_NAME=") && !content.includes("KRADLEVERSE_AGENT_NAME=\n")) {
              return "Already registered. Credentials in " + envPath
            }
          }
          return "Not registered. Provide a name to register."
        },
      }),
    },
  }
}
