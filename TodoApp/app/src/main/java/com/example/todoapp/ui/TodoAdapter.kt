package com.example.todoapp.ui

import android.annotation.SuppressLint
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.ViewGroup
import androidx.core.content.ContextCompat
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.example.todoapp.R
import com.example.todoapp.data.TodoItem
import com.example.todoapp.databinding.ItemTodoBinding

class TodoAdapter(
    private val onStarChanged: (TodoItem, Int) -> Unit,
    private val onDelete: (TodoItem) -> Unit,
    private val onDragStart: (RecyclerView.ViewHolder) -> Unit
) : ListAdapter<TodoItem, TodoAdapter.TodoViewHolder>(DIFF) {

    companion object {
        private val DIFF = object : DiffUtil.ItemCallback<TodoItem>() {
            override fun areItemsTheSame(a: TodoItem, b: TodoItem) = a.id == b.id
            override fun areContentsTheSame(a: TodoItem, b: TodoItem) = a == b
        }

        private val BOARD_COLORS = listOf(
            R.color.board_amber,
            R.color.board_teal,
            R.color.board_purple,
            R.color.board_blue,
            R.color.board_rose
        )
    }

    inner class TodoViewHolder(private val binding: ItemTodoBinding) :
        RecyclerView.ViewHolder(binding.root) {

        @SuppressLint("ClickableViewAccessibility")
        fun bind(item: TodoItem) {
            binding.tvTitle.text = item.title
            binding.tvDuration.text = if (item.durationMinutes > 0)
                formatDuration(item.durationMinutes) else "No duration set"

            // Board accent color cycles through palette by id
            val colorRes = BOARD_COLORS[(item.id % BOARD_COLORS.size).toInt()]
            val color = ContextCompat.getColor(binding.root.context, colorRes)
            binding.viewAccent.setBackgroundColor(color)
            binding.tvTitle.setTextColor(color)

            // Render stars
            val starViews = listOf(
                binding.star1, binding.star2, binding.star3, binding.star4, binding.star5
            )
            starViews.forEachIndexed { idx, view ->
                val filled = idx < item.stars
                view.setImageResource(if (filled) R.drawable.ic_star_filled else R.drawable.ic_star_outline)
                view.setColorFilter(
                    ContextCompat.getColor(binding.root.context,
                        if (filled) R.color.star_filled else R.color.star_empty)
                )
                view.setOnClickListener {
                    val newStars = if (item.stars == idx + 1) 0 else idx + 1
                    onStarChanged(item, newStars)
                }
            }

            // Long press to delete
            binding.root.setOnLongClickListener {
                onDelete(item)
                true
            }

            // Drag handle touch
            binding.dragHandle.setOnTouchListener { _, event ->
                if (event.actionMasked == MotionEvent.ACTION_DOWN) {
                    onDragStart(this)
                }
                false
            }
        }

        private fun formatDuration(minutes: Int): String {
            val h = minutes / 60
            val m = minutes % 60
            return when {
                h == 0 -> "${m}m"
                m == 0 -> "${h}h"
                else -> "${h}h ${m}m"
            }
        }
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): TodoViewHolder {
        val binding = ItemTodoBinding.inflate(LayoutInflater.from(parent.context), parent, false)
        return TodoViewHolder(binding)
    }

    override fun onBindViewHolder(holder: TodoViewHolder, position: Int) =
        holder.bind(getItem(position))

    fun moveItem(from: Int, to: Int) {
        val current = currentList.toMutableList()
        val moved = current.removeAt(from)
        current.add(to, moved)
        submitList(current)
    }

    fun currentItems(): List<TodoItem> = currentList.toList()
}
