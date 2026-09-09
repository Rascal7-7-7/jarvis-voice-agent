<?php
class OrderController {
    public function show($id) {
        $order = Order::find($id);
        return view('orders.show', ['order' => $order]);   // no 404 when null
    }
    public function index(Request $r) {
        $orders = Order::all();
        foreach ($orders as $o) { $o->customer->name; }    // N+1 query
        return view('orders.index', compact('orders'));
    }
}
