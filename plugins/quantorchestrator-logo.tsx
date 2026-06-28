// @ts-nocheck
/** @jsxImportSource @opentui/solid */
import { TextAttributes } from "@opentui/core"
import type { TuiPlugin } from "@opencode-ai/plugin/tui"
import { useTerminalDimensions } from "@opentui/solid"
import { createSignal, For, onCleanup, type JSX } from "solid-js"

const id = "quantorchestrator-logo"

const TYPE_SPEED_MS = 18
const LOOP_DELAY_MS = 2000

const rawLogo = {
  // Quant
  left: [
    "                         ",
    "█▀▀█ █  █ ▀▀▀█ █▀▀▄ ▀█▀",
    "█__█ █__█ █^^█ █__█  █ ",
    "▀▀▀█ ▀▀▀▀ ▀▀▀▀ ▀~~▀  ▀ ",
  ],

  // Orchestrator
  right: [
    "                                                              ",
    "█▀▀█ █▀▀▄ █▀▀▀ █▄▄  █▀▀█ █▀▀▀ ▀█▀ █▀▀▄ ▀▀▀█ ▀█▀ █▀▀█ █▀▀▄ ",
    "█__█ █__  █___ █  █ █^^^ ▀▀▀█  █  █__  █^^█  █  █__█ █__  ",
    "▀▀▀▀ ▀    ▀▀▀▀ ▀  ▀ ▀▀▀▀ ▀▀▀▀  ▀  ▀    ▀▀▀▀  ▀  ▀▀▀▀ ▀    ",
  ],
}

const maxLen = (lines: string[]) => Math.max(...lines.map((line) => line.length))
const pad = (line: string, width: number) => line.padEnd(width, " ")

const leftWidth = maxLen(rawLogo.left)
const rightWidth = maxLen(rawLogo.right)

const logo = {
  left: rawLogo.left.map((line) => pad(line, leftWidth)),
  right: rawLogo.right.map((line) => pad(line, rightWidth)),
}

const totalWidth = leftWidth + 1 + rightWidth

const colors = {
  left: "#8B95C7",
  right: "#ECEFF4",
  leftShadow: "#34384D",
  rightShadow: "#474C5F",
}

const maskLine = (line: string, visibleChars: number) => {
  return Array.from(line)
    .map((char, index) => (index < visibleChars ? char : " "))
    .join("")
}

const renderLine = (line: string, fg: string, shadow: string, bold: boolean): JSX.Element[] => {
  const attrs = bold ? TextAttributes.BOLD : undefined

  return Array.from(line).map((char) => {
    if (char === "_") {
      return (
        <text fg={fg} bg={shadow} attributes={attrs} selectable={false}>
          {" "}
        </text>
      )
    }

    if (char === "^") {
      return (
        <text fg={fg} bg={shadow} attributes={attrs} selectable={false}>
          ▀
        </text>
      )
    }

    if (char === "~") {
      return (
        <text fg={shadow} attributes={attrs} selectable={false}>
          ▀
        </text>
      )
    }

    if (char === ",") {
      return (
        <text fg={shadow} attributes={attrs} selectable={false}>
          ▄
        </text>
      )
    }

    return (
      <text fg={fg} attributes={attrs} selectable={false}>
        {char}
      </text>
    )
  })
}

const Logo = () => {
  const dim = useTerminalDimensions()
  const [visible, setVisible] = createSignal(0)

  let typingTimer: ReturnType<typeof setTimeout> | undefined
  let pauseTimer: ReturnType<typeof setTimeout> | undefined

  const clearTimers = () => {
    if (typingTimer) clearTimeout(typingTimer)
    if (pauseTimer) clearTimeout(pauseTimer)
  }

  const startTyping = () => {
    clearTimers()
    setVisible(0)

    const step = () => {
      setVisible((current) => {
        const next = current + 1

        if (next >= totalWidth) {
          pauseTimer = setTimeout(() => {
            startTyping()
          }, LOOP_DELAY_MS)

          return totalWidth
        }

        typingTimer = setTimeout(step, TYPE_SPEED_MS)
        return next
      })
    }

    typingTimer = setTimeout(step, TYPE_SPEED_MS)
  }

  startTyping()

  onCleanup(() => {
    clearTimers()
  })

  return (
    <box width={dim().width} flexDirection="column" alignItems="center">
      <For each={logo.left}>
        {(line, index) => {
          const leftVisible = () => Math.min(visible(), leftWidth)
          const rightVisible = () => Math.max(0, visible() - leftWidth - 1)

          return (
            <box flexDirection="row" gap={1}>
              <box flexDirection="row">
                {renderLine(maskLine(line, leftVisible()), colors.left, colors.leftShadow, false)}
              </box>

              <box flexDirection="row">
                {renderLine(
                  maskLine(logo.right[index()], rightVisible()),
                  colors.right,
                  colors.rightShadow,
                  true,
                )}
              </box>
            </box>
          )
        }}
      </For>
    </box>
  )
}

const tui: TuiPlugin = async (api) => {
  api.slots.register({
    id,
    order: 100,
    slots: {
      home_logo() {
        return <Logo />
      },
    },
  })
}

const plugin = { id, tui }

export default plugin