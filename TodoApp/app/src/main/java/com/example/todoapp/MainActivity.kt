package com.example.todoapp

import android.os.Bundle
import androidx.activity.viewModels
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.ItemTouchHelper
import androidx.recyclerview.widget.LinearLayoutManager
import com.example.todoapp.databinding.ActivityMainBinding
import com.example.todoapp.ui.AddTodoDialog
import com.example.todoapp.ui.DeleteConfirmDialog
import com.example.todoapp.ui.DragDropCallback
import com.example.todoapp.ui.TodoAdapter
import com.example.todoapp.viewmodel.TodoViewModel
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val viewModel: TodoViewModel by viewModels()
    private lateinit var adapter: TodoAdapter
    private lateinit var touchHelper: ItemTouchHelper

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        setSupportActionBar(binding.toolbar)

        adapter = TodoAdapter(
            onStarChanged = { item, stars -> viewModel.updateStars(item, stars) },
            onDelete = { item -> DeleteConfirmDialog.show(this, item) { viewModel.deleteTodo(item) } },
            onDragStart = { vh -> touchHelper.startDrag(vh) }
        )

        val dragDropCallback = DragDropCallback(adapter) { reordered ->
            viewModel.persistOrder(reordered)
        }
        touchHelper = ItemTouchHelper(dragDropCallback)

        binding.recyclerView.apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = this@MainActivity.adapter
            touchHelper.attachToRecyclerView(this)
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                viewModel.todos.collect { todos ->
                    adapter.submitList(todos)
                    binding.tvEmpty.visibility =
                        if (todos.isEmpty()) android.view.View.VISIBLE else android.view.View.GONE
                }
            }
        }

        binding.fab.setOnClickListener {
            AddTodoDialog.show(this) { title, stars, duration ->
                viewModel.addTodo(title, stars, duration)
            }
        }
    }
}
