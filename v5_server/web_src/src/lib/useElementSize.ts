import { useCallback, useEffect, useRef, useState } from 'react'

/** 用 ResizeObserver 跟踪元素尺寸；支持条件渲染与重新挂载，自适应响应宽高变化 */
export function useElementSize<T extends HTMLElement>() {
  const [element, setElement] = useState<T | null>(null)
  const domRef = useRef<T | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })

  const refCallback = useCallback((node: T | null) => {
    domRef.current = node
    setElement(node)
    if (node) {
      const w = node.clientWidth
      const h = node.clientHeight
      if (w > 0 || h > 0) {
        setSize({ width: w, height: h })
      }
    }
  }, [])

  // 挂载 .current 属性到 refCallback 上，使得 ref 既可以作为 React ref 属性传给 JSX，又支持 ref.current 读取
  const ref = Object.assign(refCallback, {
    get current() {
      return domRef.current
    },
    set current(val: T | null) {
      domRef.current = val
      setElement(val)
    },
  })

  useEffect(() => {
    // 兼容可能直接使用 ref.current 的传统场景
    const target = element || domRef.current
    if (!target) return

    const sync = () => {
      const w = target.clientWidth
      const h = target.clientHeight
      if (w > 0 || h > 0) {
        setSize({ width: w, height: h })
      }
    }
    sync()

    const observer = new ResizeObserver(sync)
    observer.observe(target)
    return () => observer.disconnect()
  }, [element])

  return { ref, ...size }
}

