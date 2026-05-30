package com.example.todoapp.ui

import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.RecyclerView
import com.example.todoapp.data.TodoItem

class DragDropCallback(
    private val adapter: TodoAdapter,
    private val onDropped: (List<TodoItem>) -> Unit
) : ItemTouchHelper.SimpleCallback(
    ItemTouchHelper.UP or ItemTouchHelper.DOWN, 0
) {
    override fun onMove(
        rv: RecyclerView,
        viewHolder: RecyclerView.ViewHolder,
        target: RecyclerView.ViewHolder
    ): Boolean {
        val from = viewHolder.adapterPosition
        val to = target.adapterPosition
        val items = adapter.currentItems()
        // Only allow dragging within the same star group
        if (items[from].stars != items[to].stars) return false
        adapter.moveItem(from, to)
        return true
    }

    override fun onSwiped(viewHolder: RecyclerView.ViewHolder, direction: Int) = Unit

    override fun clearView(recyclerView: RecyclerView, viewHolder: RecyclerView.ViewHolder) {
        super.clearView(recyclerView, viewHolder)
        onDropped(adapter.currentItems())
    }

    override fun isLongPressDragEnabled() = false // we use manual drag handle
}
