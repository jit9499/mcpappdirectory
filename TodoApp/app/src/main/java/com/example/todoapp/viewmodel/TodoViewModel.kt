package com.example.todoapp.viewmodel

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.example.todoapp.data.TodoDatabase
import com.example.todoapp.data.TodoItem
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class TodoViewModel(application: Application) : AndroidViewModel(application) {

    private val dao = TodoDatabase.getDatabase(application).todoDao()

    val todos: StateFlow<List<TodoItem>> = dao.getAllTodos()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), emptyList())

    fun addTodo(title: String, stars: Int, durationMinutes: Int) {
        viewModelScope.launch {
            val nextOrder = (todos.value.maxOfOrNull { it.manualOrder } ?: -1) + 1
            dao.insert(TodoItem(title = title, stars = stars, durationMinutes = durationMinutes, manualOrder = nextOrder))
        }
    }

    fun updateStars(todo: TodoItem, stars: Int) {
        viewModelScope.launch {
            dao.update(todo.copy(stars = stars))
        }
    }

    fun deleteTodo(todo: TodoItem) {
        viewModelScope.launch {
            dao.delete(todo)
        }
    }

    /** Persist new manual order after a drag-and-drop reorder within a star group. */
    fun persistOrder(reorderedList: List<TodoItem>) {
        viewModelScope.launch {
            reorderedList.forEachIndexed { index, item ->
                dao.update(item.copy(manualOrder = index))
            }
        }
    }
}
