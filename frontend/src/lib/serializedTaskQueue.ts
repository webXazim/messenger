export type SerializedTaskQueue = {
  enqueue<T>(task: () => Promise<T>): Promise<T>;
};

export function createSerializedTaskQueue(): SerializedTaskQueue {
  let tail: Promise<void> = Promise.resolve();

  return {
    enqueue<T>(task: () => Promise<T>) {
      const result = tail.catch(() => undefined).then(task);
      tail = result.then(() => undefined, () => undefined);
      return result;
    },
  };
}
