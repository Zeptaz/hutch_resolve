import { createContext, useContext } from 'react'

/** Lets the case packet ask the queue to reload after a review change, without sharing state. */
export const QueueSignal = createContext<{ tick: number; refreshQueue: () => void }>({ tick: 0, refreshQueue: () => {} })

export const useQueueSignal = () => useContext(QueueSignal)
