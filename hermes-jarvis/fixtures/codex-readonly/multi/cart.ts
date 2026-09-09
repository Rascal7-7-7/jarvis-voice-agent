interface Item { id: number; price: number; qty?: number }
export function total(items: Item[]): number {
  return items.reduce((s, i) => s + i.price * i.qty, 0);   // qty possibly undefined
}
export function find(items: Item[], id: string): Item {
  return items.find(i => i.id === id);                     // string vs number; may be undefined
}
