// @ts-nocheck
/** @jsxImportSource @opentui/solid */
import { TextAttributes } from "@opentui/core"
import type { TuiPlugin } from "@opencode-ai/plugin/tui"
import { useTerminalDimensions } from "@opentui/solid"
import { For, type JSX } from "solid-js"

const id = "quantorchestrator-logo"

const logo = {
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

const colors = {
  left: "#8B95C7",
  right: "#ECEFF4",
  leftShadow: "#34384D",
  rightShadow: "#474C5F",
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

  return (
    <box width={dim().width} flexDirection="column" alignItems="center">
      <For each={logo.left}>
        {(line, index) => (
          <box flexDirection="row" gap={1}>
            <box flexDirection="row">{renderLine(line, colors.left, colors.leftShadow, false)}</box>
            <box flexDirection="row">
              {renderLine(logo.right[index()], colors.right, colors.rightShadow, true)}
            </box>
          </box>
        )}
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
